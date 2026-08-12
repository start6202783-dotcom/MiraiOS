"""Testes do Failure Lab controlado da v0.14."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mirai.errors import MiraiRuntimeError
from mirai.failure_lab import load_failure_lab_report, run_failure_lab
from mirai.storage import stable_file_digest


def test_failure_lab_rejects_faults_and_preserves_original(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "failure-lab.json"
    before = stable_file_digest(dummy_model)

    report = run_failure_lab(
        dummy_model,
        output,
        input_specs=["3.0"],
        benchmark_runs=2,
    )

    assert report["status"] == "passed"
    assert stable_file_digest(dummy_model) == before
    assert {item["name"] for item in report["checks"]} == {
        "corruption-detected",
        "truncation-detected",
        "repeatable-output",
        "benchmark-smoke",
        "original-unchanged",
    }
    assert all(item["passed"] for item in report["checks"])
    assert report["configuration"]["inputs_included"] is False
    assert "input_specs" not in report["configuration"]


def test_failure_lab_loader_detects_tampering(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "failure-lab.json"
    run_failure_lab(dummy_model, output, benchmark_runs=1)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["status"] = "failed"
    output.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="status|identificador"):
        load_failure_lab_report(output)
