"""Testes do fluxo transacional Mirai Pilot."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from mirai.agent import create_agent_server
from mirai.agent_client import (
    activate_deployment,
    deploy_model,
    get_deployment_status,
)
from mirai.cli import main
from mirai.devices import Device, add_device
from mirai.errors import MiraiRuntimeError
from mirai.pilot import (
    launch_artifact,
    load_pilot_config,
    run_pilot,
    write_pilot_template,
)


@pytest.fixture
def pilot_device(tmp_path: Path) -> Iterator[Device]:
    """Inicia um Agent isolado para os testes do Pilot."""
    server = create_agent_server(
        "127.0.0.1",
        0,
        tmp_path / "pilot-agent-data",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield Device(name="pilot-local", url=f"http://{host}:{port}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _register_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    device: Device,
) -> None:
    monkeypatch.setenv("MIRAI_HOME", str(tmp_path / "mirai-home"))
    add_device(device.name, device.url)


def _write_config(
    path: Path,
    artifact: Path,
    device: Device,
    *,
    expected_result: object = 6.0,
) -> Path:
    payload = {
        "schema_version": 1,
        "name": "acceptance-lab",
        "artifact": artifact.name,
        "device": device.name,
        "inputs": ["5.0"],
        "layout": "auto",
        "benchmark": {"runs": 3, "warmup": 1},
        "acceptance": {
            "expected_result": expected_result,
            "result_tolerance": 1e-6,
            "max_p95_ms": 10_000.0,
            "min_ips": 0.0,
        },
        "report": {"directory": "reports"},
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def test_pilot_template_is_safe_and_does_not_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "mirai-pilot.json"

    created = write_pilot_template(target)
    payload = json.loads(created.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["benchmark"]["runs"] == 20
    assert payload["report"]["directory"] == ".mirai/reports"
    assert payload["report"]["include_inputs"] is False
    with pytest.raises(MiraiRuntimeError, match="arquivo já existe"):
        write_pilot_template(target)


def test_pilot_config_rejects_unknown_fields(tmp_path: Path) -> None:
    target = write_pilot_template(tmp_path / "mirai-pilot.json")
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["comando_perigoso"] = "ignorar-validação"
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="campos desconhecidos"):
        load_pilot_config(target)


@pytest.mark.parametrize(
    "invalid_json, message",
    [
        ('{"schema_version": 1, "schema_version": 1}', "chave JSON duplicada"),
        ('{"schema_version": 1, "acceptance": {"expected_result": NaN}}',
         "número não finito"),
    ],
)
def test_pilot_config_rejects_ambiguous_or_non_standard_json(
    tmp_path: Path,
    invalid_json: str,
    message: str,
) -> None:
    target = tmp_path / "invalid-pilot.json"
    target.write_text(invalid_json, encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match=message):
        load_pilot_config(target)


def test_launch_runs_full_happy_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pilot_device: Device,
    dummy_package: Path,
) -> None:
    _register_device(tmp_path, monkeypatch, pilot_device)

    result = launch_artifact(
        dummy_package,
        pilot_device.name,
        ["5.0"],
    )
    status = get_deployment_status(pilot_device)

    assert result.inference is not None
    assert result.inference["result"] == 6.0
    assert status["active_deployment_id"] == result.deployment["deployment_id"]


def test_pilot_approves_and_writes_auditable_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pilot_device: Device,
    dummy_package: Path,
) -> None:
    _register_device(tmp_path, monkeypatch, pilot_device)
    config_path = _write_config(
        tmp_path / "mirai-pilot.json",
        dummy_package,
        pilot_device,
    )

    outcome = run_pilot(load_pilot_config(config_path))
    json_report = json.loads(outcome.report_json.read_text(encoding="utf-8"))
    markdown_report = outcome.report_markdown.read_text(encoding="utf-8")

    assert outcome.success is True
    assert json_report["status"] == "passed"
    assert json_report["acceptance"]["passed"] is True
    assert json_report["artifact"]["sha256"]
    assert json_report["benchmark"]["runs"] == 3
    assert json_report["project"]["inputs"] is None
    assert json_report["project"]["inputs_recorded"] is False
    assert "Relatório Mirai Pilot" in markdown_report
    assert "O piloto cumpriu todos os critérios" in markdown_report


def test_failed_pilot_restores_previous_active_deployment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pilot_device: Device,
    dummy_model: Path,
    model_factory: Callable[[str, list[int | str | None], int], Path],
) -> None:
    _register_device(tmp_path, monkeypatch, pilot_device)
    previous = deploy_model(pilot_device, dummy_model)
    activate_deployment(pilot_device, previous["deployment_id"])
    candidate = model_factory("candidate", [1], 1)
    config_path = _write_config(
        tmp_path / "mirai-pilot.json",
        candidate,
        pilot_device,
        expected_result=999.0,
    )

    outcome = run_pilot(load_pilot_config(config_path))
    status = get_deployment_status(pilot_device)

    assert outcome.success is False
    assert outcome.report["rollback"]["status"] == "restored"
    assert status["active_deployment_id"] == previous["deployment_id"]


def test_failed_first_pilot_leaves_no_active_deployment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pilot_device: Device,
    dummy_package: Path,
) -> None:
    _register_device(tmp_path, monkeypatch, pilot_device)
    config_path = _write_config(
        tmp_path / "mirai-pilot.json",
        dummy_package,
        pilot_device,
        expected_result=-1.0,
    )

    outcome = run_pilot(load_pilot_config(config_path))
    status = get_deployment_status(pilot_device)

    assert outcome.success is False
    assert outcome.report["rollback"]["status"] == "deactivated"
    assert status["active_deployment_id"] is None


def test_cli_initializes_pilot_project(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "custom-pilot.json"

    assert main(["pilot", "init", str(target)]) == 0

    assert target.is_file()
    assert "Projeto de piloto criado" in capsys.readouterr().out
