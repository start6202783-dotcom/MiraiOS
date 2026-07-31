"""Testes do histórico consultável, retenção e visão de frota."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mirai.agent_client import deployment_retention_candidates
from mirai.devices import Device
from mirai.errors import MiraiRuntimeError
from mirai.fleet import inspect_fleet
from mirai.history import get_pilot_report, list_pilot_history, prune_pilot_history


def _report(
    directory: Path,
    run_id: str,
    *,
    project: str = "edge-lab",
    status: str = "passed",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{project}-{run_id}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "started_at": run_id[:8] + "T00:00:00+00:00",
                "finished_at": run_id[:8] + "T00:01:00+00:00",
                "status": status,
                "project": {"name": project, "device": "edge-1"},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_history_returns_newest_first(tmp_path: Path) -> None:
    _report(tmp_path, "20260729T120000Z-aaaaaaaa")
    newest = _report(tmp_path, "20260731T120000Z-bbbbbbbb")

    entries = list_pilot_history(tmp_path)

    assert entries[0]["run_id"] == "20260731T120000Z-bbbbbbbb"
    assert entries[0]["report_json"] == newest


def test_history_filters_status(tmp_path: Path) -> None:
    _report(tmp_path, "20260729T120000Z-aaaaaaaa", status="failed")
    _report(tmp_path, "20260731T120000Z-bbbbbbbb", status="passed")

    entries = list_pilot_history(tmp_path, status="failed")

    assert [item["status"] for item in entries] == ["failed"]


def test_history_honors_limit(tmp_path: Path) -> None:
    _report(tmp_path, "20260729T120000Z-aaaaaaaa")
    _report(tmp_path, "20260730T120000Z-bbbbbbbb")

    assert len(list_pilot_history(tmp_path, limit=1)) == 1


def test_history_missing_directory_is_empty(tmp_path: Path) -> None:
    assert list_pilot_history(tmp_path / "missing") == []


@pytest.mark.parametrize("status", ["running", "unknown", "PASS"])
def test_history_rejects_unsupported_filter(tmp_path: Path, status: str) -> None:
    with pytest.raises(MiraiRuntimeError, match="status deve ser"):
        list_pilot_history(tmp_path, status=status)


def test_get_pilot_report_uses_exact_run_id(tmp_path: Path) -> None:
    run_id = "20260731T120000Z-aaaaaaaa"
    _report(tmp_path, run_id)

    assert get_pilot_report(tmp_path, run_id)["run_id"] == run_id


@pytest.mark.parametrize("run_id", ["../secret", "", "2026-07-31"])
def test_get_pilot_report_rejects_path_like_ids(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(MiraiRuntimeError, match="run_id inválido"):
        get_pilot_report(tmp_path, run_id)


def test_pilot_prune_defaults_to_dry_run(tmp_path: Path) -> None:
    old = _report(tmp_path, "20260729T120000Z-aaaaaaaa")
    old.with_suffix(".md").write_text("report", encoding="utf-8")
    old.with_name(old.name + ".sig").write_text("signature", encoding="utf-8")
    _report(tmp_path, "20260731T120000Z-bbbbbbbb")

    candidates = prune_pilot_history(tmp_path, keep=1)

    assert len(candidates) == 3
    assert old.exists()


def test_pilot_prune_removes_only_known_siblings(tmp_path: Path) -> None:
    old = _report(tmp_path, "20260729T120000Z-aaaaaaaa")
    unrelated = tmp_path / "keep-me.txt"
    unrelated.write_text("safe", encoding="utf-8")
    _report(tmp_path, "20260731T120000Z-bbbbbbbb")

    prune_pilot_history(tmp_path, keep=1, apply=True)

    assert not old.exists()
    assert unrelated.exists()


def test_deployment_retention_never_selects_active() -> None:
    status = {
        "active_deployment_id": "active",
        "deployments": [
            {"deployment_id": "active", "created_at": "2026-07-01"},
            {"deployment_id": "new", "created_at": "2026-07-03"},
            {"deployment_id": "old", "created_at": "2026-07-02"},
        ],
    }

    candidates = deployment_retention_candidates(status, keep=1)

    assert [item["deployment_id"] for item in candidates] == ["old"]


def test_fleet_inspects_devices_in_stable_name_order() -> None:
    devices = {
        "b": Device("b", "http://127.0.0.1:2"),
        "a": Device("a", "http://127.0.0.1:1"),
    }

    def doctor(device: Device) -> dict[str, object]:
        return {
            "compatible": True,
            "info": {
                "machine": "aarch64",
                "hardware_profile": {"profile": "arm64-cpu"},
                "providers": ["CPUExecutionProvider"],
            },
            "deployments": {
                "active_deployment_id": f"active-{device.name}",
                "deployments": [{"deployment_id": "one"}],
            },
        }

    result = inspect_fleet(devices, doctor=doctor)

    assert [item["name"] for item in result] == ["a", "b"]
    assert result[0]["hardware_profile"]["profile"] == "arm64-cpu"


def test_fleet_keeps_offline_device_as_result() -> None:
    device = Device("offline", "http://127.0.0.1:1")

    def doctor(_: Device) -> dict[str, object]:
        raise MiraiRuntimeError("connection refused")

    result = inspect_fleet({device.name: device}, doctor=doctor)

    assert result[0]["status"] == "offline"
    assert "connection refused" in result[0]["error"]


def test_fleet_empty_registry_is_empty() -> None:
    assert inspect_fleet({}) == []
