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
    get_agent_info,
    get_agent_logs,
    get_deployment_status,
    request_json,
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


def test_agent_health_and_info(agent_device: Device) -> None:
    health = request_json(agent_device, "/v1/health")
    info = get_agent_info(agent_device)

    assert health["status"] == "ok"
    assert info["machine"]
    assert "CPUExecutionProvider" in info["providers"]


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
