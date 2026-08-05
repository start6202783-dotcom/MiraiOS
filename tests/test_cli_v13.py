"""Testes de roteamento e experiência da CLI adicionada na v0.13."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import mirai.cli as cli_module
from mirai.cli import main
from mirai.devices import Device, get_device
from mirai.fit import FitOutcome


def test_cli_device_tag_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MIRAI_HOME", str(tmp_path / "home"))
    assert main(["device", "add", "edge", "--url", "http://127.0.0.1:8080"]) == 0

    assert main(["device", "tag", "edge", "--set", "env=prod", "--set", "zone=sp"]) == 0
    assert main(["device", "list"]) == 0
    assert main(["device", "tag", "edge", "--remove", "zone"]) == 0

    assert get_device("edge").tags == ("env=prod",)
    output = capsys.readouterr().out
    assert "env=prod" in output
    assert "zone=sp" in output


def test_cli_fit_reports_accepted_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "output.mirai.fit.json"
    package_path = tmp_path / "output.mirai"
    outcome = FitOutcome(
        accepted=True,
        package_path=package_path,
        signature_path=None,
        report_path=report_path,
        report={
            "benchmark": {"p95_speedup": 1.25},
            "quality": {"max_absolute_error": 0.01},
        },
    )
    monkeypatch.setattr(cli_module, "fit_model", lambda *_args, **_kwargs: outcome)

    code = main(
        [
            "fit",
            str(tmp_path / "source.onnx"),
            "--name",
            "edge",
            "--package-version",
            "1.0.0",
            "--output",
            str(package_path),
        ]
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "1.250x" in output
    assert "Variante aprovada" in output


def test_cli_fit_returns_failure_when_gates_reject(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = FitOutcome(
        accepted=False,
        package_path=None,
        signature_path=None,
        report_path=tmp_path / "rejected.fit.json",
        report={
            "benchmark": {"p95_speedup": 0.5},
            "quality": {"max_absolute_error": 0.2},
        },
    )
    monkeypatch.setattr(cli_module, "fit_model", lambda *_args, **_kwargs: outcome)

    code = main(
        [
            "fit",
            "source.onnx",
            "--name",
            "edge",
            "--package-version",
            "1.0.0",
            "--output",
            "output.mirai",
        ]
    )

    assert code == 1
    assert "variante rejeitada" in capsys.readouterr().err


def test_cli_rollout_defaults_to_plan_and_prints_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, Any] = {}

    def rollout(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "run_id": "run-1",
            "status": "planned",
            "batches": [["edge"]],
            "report_path": str(tmp_path / "run-1.json"),
        }

    monkeypatch.setattr(cli_module, "execute_rollout", rollout)
    monkeypatch.setattr(
        cli_module,
        "load_devices",
        lambda: {"edge": Device("edge", "http://127.0.0.1:8080")},
    )

    code = main(["fleet", "rollout", "model.onnx", "--selector", "env=prod"])

    assert code == 0
    assert captured["apply"] is False
    assert captured["selector"] == "env=prod"
    assert "Revise o plano" in capsys.readouterr().out


@pytest.mark.parametrize("status", ["rolled_back", "rollback_failed"])
def test_cli_rollout_failure_status_returns_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "execute_rollout",
        lambda *_args, **_kwargs: {
            "run_id": "run-1",
            "status": status,
            "batches": [["edge"]],
            "report_path": "report.json",
        },
    )
    monkeypatch.setattr(cli_module, "load_devices", lambda: {})

    assert main(["fleet", "rollout", "model.onnx", "--apply"]) == 1
    assert "rollout interrompido" in capsys.readouterr().err


def test_cli_fleet_anchor_preserves_partial_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli_module,
        "load_devices",
        lambda: {"edge": Device("edge", "http://127.0.0.1:8080")},
    )
    monkeypatch.setattr(
        cli_module,
        "select_devices",
        lambda devices, selector: list(devices.values()),
    )
    monkeypatch.setattr(
        cli_module,
        "anchor_fleet",
        lambda *_args, **_kwargs: [
            {"device": "edge", "status": "failed", "error": "offline"}
        ],
    )

    assert main(["fleet", "anchor"]) == 1
    assert "offline" in capsys.readouterr().out


def test_cli_audit_anchor_prints_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    device = Device("edge", "http://127.0.0.1:8080")
    monkeypatch.setattr(cli_module, "get_device", lambda _: device)
    monkeypatch.setattr(
        cli_module,
        "anchor_device",
        lambda *_args, **_kwargs: {"status": "anchored", "records": 2, "head": "f" * 64},
    )

    assert main(["audit", "anchor", "--device", "edge"]) == 0
    output = capsys.readouterr().out
    assert "anchored" in output
    assert "Registros: 2" in output


@pytest.mark.parametrize(
    "arguments",
    [
        ["fleet", "rollout", "model.onnx", "--max-failure-rate", "nan"],
        ["fleet", "rollout", "model.onnx", "--max-failure-rate", "inf"],
        [
            "fit",
            "model.onnx",
            "--name",
            "x",
            "--package-version",
            "1.0.0",
            "--output",
            "x.mirai",
            "--min-speedup",
            "nan",
        ],
    ],
)
def test_cli_rejects_non_finite_policy_values(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(arguments)

    assert exit_info.value.code == 2
