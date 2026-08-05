"""Integração HTTP dos endpoints de auditoria e observabilidade da v0.13."""

from __future__ import annotations

import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from mirai.agent import create_agent_server
from mirai.agent_client import (
    activate_deployment,
    deploy_model,
    get_agent_audit,
    get_agent_drift,
    get_agent_health,
    get_agent_metrics,
    run_remote_model,
)
from mirai.anchors import anchor_device
from mirai.devices import Device
from mirai.errors import MiraiRuntimeError


@pytest.fixture
def v13_agent(tmp_path: Path) -> Iterator[tuple[Device, Path]]:
    data_dir = tmp_path / "agent-data"
    server = create_agent_server("127.0.0.1", 0, data_dir)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield Device("local", f"http://{host}:{port}"), data_dir
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _raw_get(device: Device, path: str) -> tuple[int, dict[str, str], bytes]:
    parsed = urlsplit(device.url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    connection.request("GET", path)
    response = connection.getresponse()
    status = response.status
    headers = {key.lower(): value for key, value in response.getheaders()}
    body = response.read()
    connection.close()
    return status, headers, body


def test_agent_exposes_structured_metrics_and_drift(
    v13_agent: tuple[Device, Path],
    dummy_model: Path,
) -> None:
    device, _ = v13_agent
    deployment = deploy_model(device, dummy_model)
    activate_deployment(device, deployment["deployment_id"])
    run_remote_model(device, ["5.0"], "auto")

    metrics = get_agent_metrics(device)
    drift = get_agent_drift(device)
    observed = metrics["deployments"][deployment["deployment_id"]]

    assert metrics["counters"]["deployments_total"] == 1
    assert metrics["counters"]["activations_total"] == 1
    assert metrics["counters"]["inferences_total"] == 1
    assert observed["inferences_total"] == 1
    assert observed["latency"]["p95_ms"] >= 0
    assert drift["deployments"][deployment["deployment_id"]]["output"]["status"] == (
        "insufficient_data"
    )


def test_agent_prometheus_endpoint_is_plain_text_and_not_cached(
    v13_agent: tuple[Device, Path],
    dummy_model: Path,
) -> None:
    device, _ = v13_agent
    deployment = deploy_model(device, dummy_model)
    activate_deployment(device, deployment["deployment_id"])
    run_remote_model(device, ["5.0"], "auto")

    status, headers, body = _raw_get(device, "/metrics")
    payload = body.decode("utf-8")

    assert status == 200
    assert headers["content-type"].startswith("text/plain; version=0.0.4")
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert "mirai_agent_inferences_total" in payload
    assert deployment["deployment_id"] in payload


def test_failed_inference_updates_failure_metrics(
    v13_agent: tuple[Device, Path],
    dummy_model: Path,
) -> None:
    device, _ = v13_agent
    deployment = deploy_model(device, dummy_model)
    activate_deployment(device, deployment["deployment_id"])

    with pytest.raises(MiraiRuntimeError):
        run_remote_model(device, ["not-a-number"], "auto")

    metrics = get_agent_metrics(device)
    observed = metrics["deployments"][deployment["deployment_id"]]
    assert metrics["counters"]["inferences_total"] == 1
    assert metrics["counters"]["inference_failures_total"] == 1
    assert observed["error_rate"] == 1.0


def test_agent_audit_endpoint_returns_verifiable_extension(
    v13_agent: tuple[Device, Path],
    dummy_model: Path,
) -> None:
    device, _ = v13_agent
    deployment = deploy_model(device, dummy_model)
    anchored = get_agent_audit(device)
    activate_deployment(device, deployment["deployment_id"])
    run_remote_model(device, ["5.0"], "auto")

    extension = get_agent_audit(
        device,
        from_records=anchored["records"],
        from_head=anchored["head"],
    )

    assert extension["records"] == anchored["records"] + 2
    assert len(extension["proof"]) == 2
    assert extension["proof"][0]["previous_hash"] == anchored["head"]
    assert extension["proof"][-1]["record_hash"] == extension["head"]


def test_local_agent_can_be_anchored_with_its_persistent_identity(
    v13_agent: tuple[Device, Path],
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    device, _ = v13_agent
    deployment = deploy_model(device, dummy_model)
    ledger = tmp_path / "control-plane" / "anchors.jsonl"

    first = anchor_device(device, ledger_path=ledger)
    activate_deployment(device, deployment["deployment_id"])
    second = anchor_device(device, ledger_path=ledger)

    assert first["status"] == "anchored"
    assert second["status"] == "anchored"
    assert second["agent_id"] == get_agent_health(device)["agent_id"]
    assert second["records"] == first["records"] + 1


@pytest.mark.parametrize(
    "path",
    [
        "/v1/audit?from_records=0",
        "/v1/audit?from_head=" + "0" * 64,
        "/v1/audit?from_records=x&from_head=" + "0" * 64,
        "/v1/audit?from_records=0&from_records=1&from_head=" + "0" * 64,
        "/v1/audit?unknown=1",
    ],
)
def test_agent_audit_rejects_ambiguous_or_invalid_query(
    v13_agent: tuple[Device, Path],
    path: str,
) -> None:
    device, _ = v13_agent

    status, _, body = _raw_get(device, path)

    assert status == 400
    assert json.loads(body)["error"]


def test_agent_client_requires_complete_audit_checkpoint(
    v13_agent: tuple[Device, Path],
) -> None:
    device, _ = v13_agent

    with pytest.raises(MiraiRuntimeError, match="juntos"):
        get_agent_audit(device, from_records=0)


def test_agent_flushes_observability_during_clean_shutdown(
    tmp_path: Path,
    dummy_model: Path,
) -> None:
    data_dir = tmp_path / "persistent-agent"
    first = create_agent_server("127.0.0.1", 0, data_dir)
    first_thread = threading.Thread(target=first.serve_forever, daemon=True)
    first_thread.start()
    host, port = first.server_address[:2]
    device = Device("persistent", f"http://{host}:{port}")
    deployment = deploy_model(device, dummy_model)
    activate_deployment(device, deployment["deployment_id"])
    run_remote_model(device, ["5.0"], "auto")
    first.shutdown()
    first.server_close()
    first_thread.join(timeout=5)

    second = create_agent_server("127.0.0.1", 0, data_dir)
    second_thread = threading.Thread(target=second.serve_forever, daemon=True)
    second_thread.start()
    second_host, second_port = second.server_address[:2]
    restarted = Device("persistent", f"http://{second_host}:{second_port}")
    try:
        metrics = get_agent_metrics(restarted)
    finally:
        second.shutdown()
        second.server_close()
        second_thread.join(timeout=5)

    assert metrics["counters"]["inferences_total"] == 1
    assert metrics["deployments"][deployment["deployment_id"]]["inferences_total"] == 1


def test_health_still_remains_public_while_metrics_use_viewer_policy(
    v13_agent: tuple[Device, Path],
) -> None:
    device, _ = v13_agent

    health = get_agent_health(device)

    assert health["status"] == "ok"
    assert len(health["agent_id"]) == 32
