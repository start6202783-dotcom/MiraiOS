"""Contratos da v0.12: admissão, quarentena, auditoria e persistência."""

from __future__ import annotations

import base64
import json
import os
import threading
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path

import pytest
from onnx import TensorProto, external_data_helper, helper, numpy_helper

from mirai.admission import AdmissionPolicy, admit_artifact
from mirai.agent import (
    AgentRequestError,
    AgentState,
    _install_verified_file,
    create_agent_server,
)
from mirai.agent_client import (
    activate_deployment,
    deploy_model,
    get_agent_audit,
    request_json,
    run_remote_model,
)
from mirai.audit import AuditLog
from mirai.devices import Device
from mirai.errors import MiraiRuntimeError
from mirai.inspect import validate_model, validate_model_with_report
from mirai.json_codec import canonical_json_bytes, strict_json_dumps, strict_json_loads
from mirai.signing import generate_signing_key, sign_artifact
from mirai.storage import atomic_write_text, stable_file_digest, verify_file


def _signed_package(
    package: Path,
    tmp_path: Path,
) -> tuple[Path, Path, Path]:
    private = tmp_path / "release.key"
    public = tmp_path / "release.pub"
    generate_signing_key(private, public)
    signed = sign_artifact(package, private)
    return private, public, Path(signed["signature"])


def test_strict_json_rejects_duplicate_keys() -> None:
    with pytest.raises(MiraiRuntimeError, match="duplicada"):
        strict_json_loads('{"role":"viewer","role":"admin"}')


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_strict_json_rejects_non_finite_numbers(constant: str) -> None:
    with pytest.raises(MiraiRuntimeError, match="não finito"):
        strict_json_loads(f'{{"value":{constant}}}')


def test_strict_json_limits_depth() -> None:
    with pytest.raises(MiraiRuntimeError, match="profundidade"):
        strict_json_loads("[[[[1]]]]", max_depth=3)


def test_strict_json_limits_nodes() -> None:
    with pytest.raises(MiraiRuntimeError, match="valores"):
        strict_json_loads("[1,2,3]", max_nodes=3)


def test_strict_json_wraps_encoder_recursion_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_recursively(*args: object, **kwargs: object) -> str:
        raise RecursionError("synthetic recursion limit")

    monkeypatch.setattr("mirai.json_codec.json.dumps", fail_recursively)
    with pytest.raises(MiraiRuntimeError, match="serializado"):
        strict_json_dumps({"value": 1})


def test_strict_json_rejects_nan_on_output() -> None:
    with pytest.raises(MiraiRuntimeError, match="serializado"):
        strict_json_dumps({"result": float("nan")})


def test_canonical_json_is_order_independent() -> None:
    assert canonical_json_bytes({"b": 2, "a": 1}) == canonical_json_bytes(
        {"a": 1, "b": 2}
    )


def test_atomic_write_replaces_content_and_sets_private_mode(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    atomic_write_text(target, "first")
    atomic_write_text(target, "second")

    assert target.read_text(encoding="utf-8") == "second"
    if os.name != "nt":
        assert target.stat().st_mode & 0o077 == 0


def test_stable_digest_and_verification(tmp_path: Path) -> None:
    target = tmp_path / "artifact.bin"
    target.write_bytes(b"mirai")
    digest, size = stable_file_digest(target)

    verify_file(
        target,
        expected_sha256=digest,
        expected_size=size,
        label="artifact",
    )
    target.write_bytes(b"tampered")
    with pytest.raises(MiraiRuntimeError, match="alterado"):
        verify_file(
            target,
            expected_sha256=digest,
            expected_size=size,
            label="artifact",
        )


@pytest.mark.skipif(os.name == "nt", reason="semântica de symlink POSIX")
def test_stable_digest_refuses_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    link = tmp_path / "link"
    target.write_bytes(b"data")
    link.symlink_to(target)
    with pytest.raises(MiraiRuntimeError, match="abrir"):
        stable_file_digest(link)


def test_content_install_is_atomic_under_concurrency(tmp_path: Path) -> None:
    source_a = tmp_path / "source-a"
    source_b = tmp_path / "source-b"
    target = tmp_path / "content-addressed-model"
    source_a.write_bytes(b"same-content")
    source_b.write_bytes(b"same-content")
    digest, size = stable_file_digest(source_a)
    barrier = threading.Barrier(2)
    outcomes: list[bool] = []
    failures: list[BaseException] = []

    def install(source: Path) -> None:
        try:
            barrier.wait()
            outcomes.append(
                _install_verified_file(
                    source,
                    target,
                    expected_sha256=digest,
                    expected_size=size,
                )
            )
        except Exception as error:  # noqa: BLE001 - coleta falhas das threads.
            failures.append(error)

    workers = [
        threading.Thread(target=install, args=(source_a,)),
        threading.Thread(target=install, args=(source_b,)),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert failures == []
    assert sorted(outcomes) == [False, True]
    assert target.read_bytes() == b"same-content"


def test_model_quarantine_reports_structure(dummy_model: Path) -> None:
    _, report = validate_model_with_report(dummy_model)

    assert report.graph_count == 1
    assert report.node_count == 1
    assert report.external_data is False


def test_model_quarantine_rejects_external_data_without_reading_it(
    tmp_path: Path,
) -> None:
    tensor = numpy_helper.from_array(
        __import__("numpy").array([1.0], dtype="float32"),
        name="weight",
    )
    external_data_helper.set_external_data(tensor, location="../../etc/passwd")
    tensor.ClearField("raw_data")
    tensor.data_location = TensorProto.EXTERNAL
    value = helper.make_tensor_value_info("weight", TensorProto.FLOAT, [1])
    graph = helper.make_graph([], "external", [], [value], [tensor])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    path = tmp_path / "external.onnx"
    path.write_bytes(model.SerializeToString())

    with pytest.raises(MiraiRuntimeError, match="dados externos"):
        validate_model(path)


def test_model_quarantine_rejects_static_tensor_bomb(
    model_factory: Callable[[str, list[int | str | None], int], Path],
) -> None:
    model = model_factory("tensor-bomb", [100_000_001])
    with pytest.raises(MiraiRuntimeError, match="elementos estáticos"):
        validate_model(model)


def test_model_quarantine_counts_typed_initializer_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tensor = helper.make_tensor("typed", TensorProto.FLOAT, [4], [1.0] * 4)
    graph = helper.make_graph([], "typed-initializer", [], [], [tensor])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    path = tmp_path / "typed-initializer.onnx"
    path.write_bytes(model.SerializeToString())
    monkeypatch.setattr("mirai.model_guard.MAX_INITIALIZER_BYTES", 1)

    with pytest.raises(MiraiRuntimeError, match="initializers excedem"):
        validate_model(path)


def test_admission_signed_accepts_trusted_package(
    dummy_package: Path,
    tmp_path: Path,
) -> None:
    _, public, signature = _signed_package(dummy_package, tmp_path)
    header = base64.b64encode(signature.read_bytes()).decode("ascii")

    result = admit_artifact(
        dummy_package,
        header,
        AdmissionPolicy("signed", (public,)),
    )

    assert result["verified"] is True
    assert len(str(result["key_id"])) == 64


def test_admission_signed_rejects_missing_signature(
    dummy_package: Path,
    tmp_path: Path,
) -> None:
    _, public, _ = _signed_package(dummy_package, tmp_path)
    with pytest.raises(MiraiRuntimeError, match="exige uma assinatura"):
        admit_artifact(dummy_package, None, AdmissionPolicy("signed", (public,)))


def test_admission_signed_rejects_raw_onnx(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    public = tmp_path / "trusted.pub"
    private = tmp_path / "trusted.key"
    generate_signing_key(private, public)
    with pytest.raises(MiraiRuntimeError, match="somente pacotes"):
        admit_artifact(dummy_model, None, AdmissionPolicy("signed", (public,)))


def test_admission_never_ignores_untrusted_signature(
    dummy_package: Path,
    tmp_path: Path,
) -> None:
    _, _, signature = _signed_package(dummy_package, tmp_path)
    header = base64.b64encode(signature.read_bytes()).decode("ascii")
    with pytest.raises(MiraiRuntimeError, match="não possui chaves"):
        admit_artifact(dummy_package, header, AdmissionPolicy())


def test_client_rejects_signature_larger_than_transport_limit(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    signature = tmp_path / "oversized.sig"
    signature.write_bytes(b"x" * 25_000)
    device = Device("offline", "http://127.0.0.1:9")

    with pytest.raises(MiraiRuntimeError, match="excede o limite"):
        deploy_model(device, dummy_model, signature_path=signature)


def test_signed_agent_enforces_proof_carrying_deployment(
    dummy_package: Path,
    tmp_path: Path,
) -> None:
    _, public, signature = _signed_package(dummy_package, tmp_path)
    server = create_agent_server(
        "127.0.0.1",
        0,
        tmp_path / "signed-agent",
        admission_policy=AdmissionPolicy("signed", (public,)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    device = Device("signed", f"http://{host}:{port}")
    try:
        with pytest.raises(MiraiRuntimeError, match="assinatura"):
            deploy_model(device, dummy_package)
        deployment = deploy_model(
            device,
            dummy_package,
            signature_path=signature,
        )
        assert deployment["admission"]["verified"] is True
        assert deployment["model_safety"]["external_data"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_agent_rejects_model_tampering_before_activation(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    server = create_agent_server("127.0.0.1", 0, tmp_path / "integrity-agent")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    device = Device("integrity", f"http://{host}:{port}")
    try:
        deployment = deploy_model(device, dummy_model)
        stored = next(server.state.models_dir.iterdir())
        stored.chmod(0o600)
        stored.write_bytes(b"tampered")
        with pytest.raises(MiraiRuntimeError, match="alterado"):
            activate_deployment(device, deployment["deployment_id"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_agent_rechecks_integrity_after_activation(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    server = create_agent_server("127.0.0.1", 0, tmp_path / "runtime-integrity")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    device = Device("integrity", f"http://{host}:{port}")
    try:
        deployment = deploy_model(device, dummy_model)
        activate_deployment(device, deployment["deployment_id"])
        assert run_remote_model(device, ["1"], "auto")["result"] == 2.0
        stored = next(server.state.models_dir.iterdir())
        stored.chmod(0o600)
        stored.write_bytes(b"tampered")
        with pytest.raises(MiraiRuntimeError, match="alterado"):
            run_remote_model(device, ["1"], "auto")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_audit_chain_detects_edit_reorder_and_truncation(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.append({"type": "first"})
    audit.append({"type": "second"})
    original = audit.path.read_bytes()

    lines = original.splitlines()
    audit.path.write_bytes(b"\n".join(reversed(lines)) + b"\n")
    with pytest.raises(MiraiRuntimeError, match="auditoria|cadeia"):
        audit.verify()

    audit.path.write_bytes(original)
    value = json.loads(lines[0])
    value["event"]["type"] = "edited"
    audit.path.write_text(json.dumps(value) + "\n" + lines[1].decode() + "\n")
    with pytest.raises(MiraiRuntimeError, match="hash"):
        audit.verify()

    audit.path.write_bytes(lines[0] + b"\n")
    with pytest.raises(MiraiRuntimeError, match="truncada"):
        audit.verify()


def test_audit_cached_head_never_hides_external_tampering(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit-cache.jsonl")
    audit.append({"type": "first"})
    audit.path.write_text('{"forged":true}\n', encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="auditoria|campos"):
        audit.append({"type": "must-not-be-written"})


def test_audit_endpoint_returns_anchor(tmp_path: Path) -> None:
    server = create_agent_server("127.0.0.1", 0, tmp_path / "audit-agent")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    device = Device("audit", f"http://{host}:{port}")
    try:
        server.state.append_event({"type": "test", "status": "success"})
        status = get_agent_audit(device)
        assert status["valid"] is True
        assert status["records"] == 1
        assert len(status["head"]) == 64
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_work_slots_fail_fast_when_capacity_is_exhausted(tmp_path: Path) -> None:
    state = AgentState(tmp_path / "slots", max_parallel_work=1)
    with state.work_slot():
        with pytest.raises(AgentRequestError) as captured, state.work_slot():
            pass
    assert captured.value.status == HTTPStatus.SERVICE_UNAVAILABLE


def test_agent_hides_python_server_version(tmp_path: Path) -> None:
    server = create_agent_server("127.0.0.1", 0, tmp_path / "headers")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    device = Device("headers", f"http://{host}:{port}")
    try:
        health = request_json(device, "/v1/health", authenticate=False)
        assert health["limits"]["requests"] == 32
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
