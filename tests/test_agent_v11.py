"""Integrações da API v0.11: anexos, RBAC, providers e retenção."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest

from mirai.agent import create_agent_server
from mirai.agent_client import (
    activate_deployment,
    delete_deployment,
    deploy_model,
    get_agent_clients,
    get_agent_info,
    get_deployment_status,
    pair_device,
    run_remote_model,
)
from mirai.devices import Device
from mirai.errors import MiraiRuntimeError


@pytest.fixture
def v11_agent(tmp_path: Path) -> Iterator[Device]:
    server = create_agent_server("127.0.0.1", 0, tmp_path / "v11-agent")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield Device("v11", f"http://{host}:{port}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _secure_role_agent(
    tmp_path: Path,
    role: str,
) -> tuple[object, threading.Thread, Device, str]:
    server = create_agent_server(
        "127.0.0.1",
        0,
        tmp_path / f"secure-{role}",
        secure=True,
        pairing_role=role,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    fingerprint = server.state.security.fingerprint
    code = server.state.security.pairing_code
    assert fingerprint is not None and code is not None
    device = Device(
        role,
        f"https://{host}:{port}",
        tls_fingerprint=fingerprint,
    )
    return server, thread, device, code


def test_agent_info_exposes_hardware_and_provider_profiles(v11_agent: Device) -> None:
    info = get_agent_info(v11_agent)

    assert info["hardware_profile"]["profile"]
    assert info["hardware_profile"]["support"] in {"validated", "experimental"}
    assert info["provider_profiles"] == ["auto", "cpu", "cuda", "directml"]


def test_deploy_records_explicit_cpu_profile(v11_agent: Device, dummy_model: Path) -> None:
    deployment = deploy_model(v11_agent, dummy_model, "cpu")

    assert deployment["provider_profile"] == "cpu"
    assert deployment["providers"] == ["CPUExecutionProvider"]


def test_deploy_rejects_unavailable_cuda(v11_agent: Device, dummy_model: Path) -> None:
    with pytest.raises(MiraiRuntimeError, match="CUDAExecutionProvider"):
        deploy_model(v11_agent, dummy_model, "cuda")


def test_remote_image_upload_runs_without_persisting_source_path(
    v11_agent: Device,
    model_factory: Callable[[str, list[int | str | None], int], Path],
    sample_image: Path,
) -> None:
    model = model_factory("remote-image", [1, 3, 8, 8])
    deployment = deploy_model(v11_agent, model)
    activate_deployment(v11_agent, deployment["deployment_id"])

    inference = run_remote_model(v11_agent, [str(sample_image)], "nchw")

    assert inference["status"] == "success"
    assert inference["result"]["shape"] == [1, 3, 8, 8]


def test_remote_json_file_becomes_numeric_input(
    v11_agent: Device,
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    source = tmp_path / "number.json"
    source.write_text("5.0", encoding="utf-8")
    deployment = deploy_model(v11_agent, dummy_model)
    activate_deployment(v11_agent, deployment["deployment_id"])

    inference = run_remote_model(v11_agent, [str(source)], "auto")

    assert inference["result"] == 6.0


def test_remote_npy_file_runs_safely(
    v11_agent: Device,
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    source = tmp_path / "number.npy"
    np.save(source, np.array([5.0], dtype=np.float32))
    deployment = deploy_model(v11_agent, dummy_model)
    activate_deployment(v11_agent, deployment["deployment_id"])

    inference = run_remote_model(v11_agent, [str(source)], "auto")

    assert inference["result"] == 6.0


def test_delete_ready_deployment_removes_registry_entry(
    v11_agent: Device,
    dummy_model: Path,
) -> None:
    deployment = deploy_model(v11_agent, dummy_model)

    deleted = delete_deployment(v11_agent, deployment["deployment_id"])
    status = get_deployment_status(v11_agent)

    assert deleted["status"] == "deleted"
    assert status["deployments"] == []


def test_delete_active_deployment_is_rejected(
    v11_agent: Device,
    dummy_model: Path,
) -> None:
    deployment = deploy_model(v11_agent, dummy_model)
    activate_deployment(v11_agent, deployment["deployment_id"])

    with pytest.raises(MiraiRuntimeError, match="ativo não pode"):
        delete_deployment(v11_agent, deployment["deployment_id"])


def test_viewer_can_read_but_cannot_deploy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dummy_model: Path,
) -> None:
    monkeypatch.setenv("MIRAI_HOME", str(tmp_path / "home-viewer"))
    server, thread, provisional, code = _secure_role_agent(tmp_path, "viewer")
    try:
        paired, pairing = pair_device(
            "viewer",
            provisional.url,
            code,
            provisional.tls_fingerprint or "",
        )
        assert pairing["role"] == "viewer"
        assert get_agent_info(paired)["agent_id"]
        with pytest.raises(MiraiRuntimeError, match="papel 'operator'"):
            deploy_model(paired, dummy_model)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_operator_can_deploy_but_cannot_list_clients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dummy_model: Path,
) -> None:
    monkeypatch.setenv("MIRAI_HOME", str(tmp_path / "home-operator"))
    server, thread, provisional, code = _secure_role_agent(tmp_path, "operator")
    try:
        paired, _ = pair_device(
            "operator",
            provisional.url,
            code,
            provisional.tls_fingerprint or "",
        )
        assert deploy_model(paired, dummy_model)["status"] == "ready"
        with pytest.raises(MiraiRuntimeError, match="papel 'admin'"):
            get_agent_clients(paired)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_admin_can_list_paired_clients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MIRAI_HOME", str(tmp_path / "home-admin"))
    server, thread, provisional, code = _secure_role_agent(tmp_path, "admin")
    try:
        paired, _ = pair_device(
            "admin",
            provisional.url,
            code,
            provisional.tls_fingerprint or "",
        )
        clients = get_agent_clients(paired)
        assert clients[0]["name"] == "admin"
        assert clients[0]["role"] == "admin"
        assert "token_sha256" not in clients[0]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
