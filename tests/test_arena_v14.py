"""Testes do contrato local da Mirai Benchmark Arena."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mirai.arena import (
    calculate_edge_score,
    create_arena_result,
    load_arena_result,
    verify_arena_result,
)
from mirai.errors import MiraiRuntimeError


def test_arena_result_is_privacy_safe_and_bound_to_artifact(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "arena.json"

    outcome = create_arena_result(
        dummy_model,
        output,
        runs=5,
        warmup_runs=1,
        input_specs=["5.0"],
        category="sbc-arm",
        precision="fp32",
    )
    report = verify_arena_result(output, dummy_model)

    assert outcome.report_path == output
    assert report["suite"]["scenario"] == "single-stream"
    assert report["suite"]["category"] == "sbc-arm"
    assert report["metrics"]["p99_ms"] >= 0
    assert report["metrics"]["samples_sha256"]
    assert report["privacy"]["hostname_included"] is False
    assert report["inputs"]["values_included"] is False
    assert "5.0" not in output.read_text(encoding="utf-8")


def test_arena_result_rejects_tampered_metrics(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "arena.json"
    create_arena_result(dummy_model, output, runs=3, warmup_runs=0)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["metrics"]["p95_ms"] = 999999
    output.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="Arena inválida|identificador"):
        load_arena_result(output)


def test_edge_score_is_explicit_sla_conformance() -> None:
    report = {
        "result_id": "result-example",
        "metrics": {
            "p95_ms": 10.0,
            "inferences_per_second": 100.0,
            "coefficient_of_variation": 0.1,
        },
    }

    passing = calculate_edge_score(
        report,
        target_p95_ms=10,
        target_ips=100,
    )
    failing = calculate_edge_score(
        report,
        target_p95_ms=5,
        target_ips=200,
    )
    near_miss = calculate_edge_score(
        report,
        target_p95_ms=9.9999,
        target_ips=100,
    )

    assert passing["score"] == 100
    assert passing["passed"] is True
    assert passing["kind"] == "sla-conformance"
    assert failing["score"] == 50
    assert failing["passed"] is False
    assert "políticas" in failing["warning"]
    assert near_miss["score"] == 100
    assert near_miss["passed"] is False


def test_edge_score_rejects_invalid_policy() -> None:
    with pytest.raises(MiraiRuntimeError, match="finitas e positivas"):
        calculate_edge_score(
            {"metrics": {}},
            target_p95_ms=0,
            target_ips=1,
        )
