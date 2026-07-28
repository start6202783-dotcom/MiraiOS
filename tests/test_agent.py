"""Testes de integração do Mirai Agent e do deploy local."""

from __future__ import annotations

import hashlib
import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from mirai.agent import create_agent_server
from mirai.agent_client import (
    activate_deployment,
    deploy_model,
    doctor_device,
    get_agent_info,
    get_agent_logs,
    get_agent_health,
    get_deployment_status,
    pair_device,
    request_json,
    revoke_remote_device,
    run_remote_model,
)
from mirai.cli import main
from mirai.devices import Device
from mirai.errors import MiraiRuntimeError


@pytest.fixture
def agent_device(tmp_path: Path) -> Iterator[Device]:
    server = create_agent_server(
        "127.0.0.1",
        0,
        tmp_path / "agent-data",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]

    try:
        yield Device(name="local", url=f"http://{host}:{port}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def secure_agent(
    tmp_path: Path,
) -> Iterator[tuple[Device, str, Path]]:
    data_dir = tmp_path / "secure-agent-data"
    server = create_agent_server(
        "127.0.0.1",
        0,
        data_dir,
        secure=True,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    fingerprint = server.state.security.fingerprint
    pairing_code = server.state.security.pairing_code
    assert fingerprint is not None
    assert pairing_code is not None
    provisional = Device(
        name="secure",
        url=f"https://{host}:{port}",
        tls_fingerprint=fingerprint,
    )

    try:
        yield provisional, pairing_code, data_dir
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_agent_health_and_info(agent_device: Device) -> None:
    health = request_json(agent_device, "/v1/health")
    info = get_agent_info(agent_device)

    assert health["status"] == "ok"
    assert info["machine"]
    assert "CPUExecutionProvider" in info["providers"]


def test_secure_agent_requires_authentication_and_pins_certificate(
    secure_agent: tuple[Device, str, Path],
) -> None:
    provisional, _, _ = secure_agent
    health = get_agent_health(provisional)

    assert health["status"] == "ok"
    assert health["tls"] is True
    assert health["auth_required"] is True
    assert health["pairing_available"] is True

    with pytest.raises(MiraiRuntimeError, match="token de acesso"):
        get_agent_info(provisional)

    invalid_token = Device(
        name=provisional.name,
        url=provisional.url,
        token="z" * 43,
        tls_fingerprint=provisional.tls_fingerprint,
        agent_id="a" * 32,
        client_id="b" * 16,
    )
    with pytest.raises(MiraiRuntimeError, match="inválido ou revogado"):
        get_agent_info(invalid_token)

    wrong_identity = Device(
        name=provisional.name,
        url=provisional.url,
        tls_fingerprint="0" * 64,
    )
    with pytest.raises(MiraiRuntimeError, match="fingerprint TLS"):
        get_agent_health(wrong_identity)


def test_secure_agent_pairs_revokes_and_never_stores_plain_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    secure_agent: tuple[Device, str, Path],
) -> None:
    provisional, pairing_code, data_dir = secure_agent
    monkeypatch.setenv("MIRAI_HOME", str(tmp_path / "cli-home"))
    paired, pairing = pair_device(
        provisional.name,
        provisional.url,
        pairing_code,
        provisional.tls_fingerprint or "",
    )

    info = get_agent_info(paired)
    report = doctor_device(paired)
    stored_clients = (data_dir / "clients.json").read_text(
        encoding="utf-8"
    )

    assert info["agent_id"] == pairing["agent_id"]
    assert report["tls"] is True
    assert report["authenticated"] is True
    assert report["compatible"] is True
    assert pairing["token"] not in stored_clients

    with pytest.raises(MiraiRuntimeError, match="já utilizado"):
        pair_device(
            "outro",
            provisional.url,
            pairing_code,
            provisional.tls_fingerprint or "",
        )

    with pytest.raises(MiraiRuntimeError, match="já está cadastrado"):
        pair_device(
            provisional.name,
            provisional.url,
            "AAAA-AAAA-AAAA",
            provisional.tls_fingerprint or "",
        )

    revoked = revoke_remote_device(paired)
    assert revoked["status"] == "revoked"
    with pytest.raises(MiraiRuntimeError, match="revogado"):
        get_agent_info(paired)


def test_secure_agent_preserves_identity_and_clients_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "persistent-secure-agent"
    cli_home = tmp_path / "cli-home"
    monkeypatch.setenv("MIRAI_HOME", str(cli_home))
    first_server = create_agent_server(
        "127.0.0.1",
        0,
        data_dir,
        secure=True,
    )
    first_thread = threading.Thread(
        target=first_server.serve_forever,
        daemon=True,
    )
    first_thread.start()
    first_host, first_port = first_server.server_address[:2]
    first_fingerprint = first_server.state.security.fingerprint
    pairing_code = first_server.state.security.pairing_code
    assert first_fingerprint is not None
    assert pairing_code is not None

    try:
        paired, _ = pair_device(
            "persistent",
            f"https://{first_host}:{first_port}",
            pairing_code,
            first_fingerprint,
        )
    finally:
        first_server.shutdown()
        first_server.server_close()
        first_thread.join(timeout=5)

    second_server = create_agent_server(
        "127.0.0.1",
        0,
        data_dir,
        secure=True,
    )
    second_thread = threading.Thread(
        target=second_server.serve_forever,
        daemon=True,
    )
    second_thread.start()
    second_host, second_port = second_server.server_address[:2]
    restarted = Device(
        name=paired.name,
        url=f"https://{second_host}:{second_port}",
        token=paired.token,
        tls_fingerprint=paired.tls_fingerprint,
        agent_id=paired.agent_id,
        client_id=paired.client_id,
    )

    try:
        info = get_agent_info(restarted)
    finally:
        second_server.shutdown()
        second_server.server_close()
        second_thread.join(timeout=5)

    assert second_server.state.security.fingerprint == first_fingerprint
    assert info["agent_id"] == paired.agent_id


def test_agent_accepts_validated_deployment(
    agent_device: Device,
    dummy_model: Path,
) -> None:
    deployment = deploy_model(agent_device, dummy_model)
    events = get_agent_logs(agent_device)

    assert deployment["status"] == "ready"
    assert deployment["model"] == "dummy.onnx"
    assert deployment["sha256"] == hashlib.sha256(
        dummy_model.read_bytes()
    ).hexdigest()
    assert deployment["deployment_id"] == deployment["sha256"][:16]
    assert events[0]["deployment_id"] == deployment["deployment_id"]
    assert get_deployment_status(agent_device)["active_deployment_id"] is None


def test_agent_activates_and_runs_deployment(
    agent_device: Device,
    dummy_model: Path,
) -> None:
    deployment = deploy_model(agent_device, dummy_model)
    activated = activate_deployment(
        agent_device,
        deployment["deployment_id"],
    )
    inference = run_remote_model(
        agent_device,
        ["5.0"],
        "auto",
        dummy_model.name,
    )
    status = get_deployment_status(agent_device)
    events = get_agent_logs(agent_device)

    assert activated["status"] == "active"
    assert status["active_deployment_id"] == deployment["deployment_id"]
    assert status["deployments"][0]["status"] == "active"
    assert inference["status"] == "success"
    assert inference["result"] == 6.0
    assert inference["latency_ms"] >= 0
    assert inference["total_ms"] >= inference["latency_ms"]
    assert events[0]["type"] == "inference"


def test_agent_preserves_active_deployment_after_restart(
    tmp_path: Path,
    dummy_model: Path,
) -> None:
    data_dir = tmp_path / "persistent-agent"
    first_server = create_agent_server("127.0.0.1", 0, data_dir)
    first_thread = threading.Thread(
        target=first_server.serve_forever,
        daemon=True,
    )
    first_thread.start()
    first_host, first_port = first_server.server_address[:2]
    first_device = Device(
        name="persistent",
        url=f"http://{first_host}:{first_port}",
    )

    try:
        deployment = deploy_model(first_device, dummy_model)
        activate_deployment(first_device, deployment["deployment_id"])
    finally:
        first_server.shutdown()
        first_server.server_close()
        first_thread.join(timeout=5)

    second_server = create_agent_server("127.0.0.1", 0, data_dir)
    second_thread = threading.Thread(
        target=second_server.serve_forever,
        daemon=True,
    )
    second_thread.start()
    second_host, second_port = second_server.server_address[:2]
    second_device = Device(
        name="persistent",
        url=f"http://{second_host}:{second_port}",
    )

    try:
        status = get_deployment_status(second_device)
        inference = run_remote_model(second_device, ["5.0"], "auto")
    finally:
        second_server.shutdown()
        second_server.server_close()
        second_thread.join(timeout=5)

    assert status["active_deployment_id"] == deployment["deployment_id"]
    assert status["deployments"][0]["status"] == "active"
    assert inference["result"] == 6.0


def test_agent_requires_active_deployment(
    agent_device: Device,
    dummy_model: Path,
) -> None:
    deploy_model(agent_device, dummy_model)

    with pytest.raises(MiraiRuntimeError, match="nenhum deployment está ativo"):
        run_remote_model(agent_device, ["5.0"], "auto")


def test_agent_rejects_remote_image_paths(
    agent_device: Device,
    dummy_model: Path,
) -> None:
    deployment = deploy_model(agent_device, dummy_model)
    activate_deployment(agent_device, deployment["deployment_id"])

    with pytest.raises(MiraiRuntimeError, match="imagens remotas"):
        run_remote_model(agent_device, ["foto.jpg"], "auto")


def test_agent_rejects_invalid_checksum(
    agent_device: Device,
    dummy_model: Path,
) -> None:
    payload = dummy_model.read_bytes()
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        int(agent_device.url.rsplit(":", maxsplit=1)[1]),
        timeout=5,
    )
    connection.request(
        "POST",
        "/v1/deployments",
        body=payload,
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(payload)),
            "X-Mirai-Model-Name": "dummy.onnx",
            "X-Mirai-SHA256": "0" * 64,
        },
    )
    response = connection.getresponse()
    body = json.loads(response.read())
    connection.close()

    assert response.status == 400
    assert "SHA-256" in body["error"]
    assert get_agent_logs(agent_device) == []


def test_cli_device_deploy_and_logs_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent_device: Device,
    dummy_model: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MIRAI_HOME", str(tmp_path / "cli-home"))

    assert (
        main(
            [
                "device",
                "add",
                agent_device.name,
                "--url",
                agent_device.url,
            ]
        )
        == 0
    )
    assert main(["device", "list"]) == 0
    assert main(["device", "info", agent_device.name]) == 0
    assert (
        main(
            [
                "deploy",
                str(dummy_model),
                "--device",
                agent_device.name,
            ]
        )
        == 0
    )
    deployment_id = hashlib.sha256(dummy_model.read_bytes()).hexdigest()[:16]
    assert main(["status", "--device", agent_device.name]) == 0
    assert (
        main(
            [
                "activate",
                deployment_id,
                "--device",
                agent_device.name,
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "run",
                str(dummy_model),
                "--device",
                agent_device.name,
                "--input",
                "5.0",
            ]
        )
        == 0
    )
    assert main(["logs", "--device", agent_device.name]) == 0
    assert main(["device", "remove", agent_device.name]) == 0

    output = capsys.readouterr().out
    assert "Dispositivo cadastrado" in output
    assert "CPUExecutionProvider" in output
    assert "Deployment pronto" in output
    assert "Deployment ativo" in output
    assert "Resultado da inferência: 6.0" in output
    assert "inference | success" in output
    assert "Dispositivo removido" in output


def test_cli_secure_pair_doctor_deploy_run_and_revoke_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    secure_agent: tuple[Device, str, Path],
    dummy_model: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    provisional, pairing_code, _ = secure_agent
    monkeypatch.setenv("MIRAI_HOME", str(tmp_path / "cli-home"))
    fingerprint = provisional.tls_fingerprint or ""

    assert (
        main(
            [
                "device",
                "pair",
                provisional.name,
                "--url",
                provisional.url,
                "--code",
                pairing_code,
                "--fingerprint",
                fingerprint,
            ]
        )
        == 0
    )
    assert main(["doctor", "--device", provisional.name]) == 0
    assert (
        main(
            [
                "deploy",
                str(dummy_model),
                "--device",
                provisional.name,
            ]
        )
        == 0
    )
    deployment_id = hashlib.sha256(dummy_model.read_bytes()).hexdigest()[:16]
    assert (
        main(
            [
                "activate",
                deployment_id,
                "--device",
                provisional.name,
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "run",
                "--device",
                provisional.name,
                "--input",
                "5.0",
            ]
        )
        == 0
    )
    assert (
        main(["device", "revoke", provisional.name])
        == 0
    )

    output = capsys.readouterr().out
    assert "Dispositivo pareado" in output
    assert "HTTPS com fingerprint fixado" in output
    assert "token pareado" in output
    assert "Deployment pronto" in output
    assert "Resultado da inferência: 6.0" in output
    assert "Credenciais revogadas" in output
