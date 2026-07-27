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
    deploy_model,
    get_agent_info,
    get_agent_logs,
    request_json,
)
from mirai.cli import main
from mirai.devices import Device


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
    assert main(["logs", "--device", agent_device.name]) == 0
    assert main(["device", "remove", agent_device.name]) == 0

    output = capsys.readouterr().out
    assert "Dispositivo cadastrado" in output
    assert "CPUExecutionProvider" in output
    assert "Deployment pronto" in output
    assert "deployment | ready" in output
    assert "Dispositivo removido" in output
