"""Testes das métricas de benchmark."""

from __future__ import annotations

from pathlib import Path

from mirai.benchmark import benchmark_model


def test_benchmark_returns_all_metrics(dummy_model: Path) -> None:
    stats = benchmark_model(
        dummy_model,
        runs=5,
        warmup_runs=2,
        input_specs=["1.0"],
    )

    assert stats.runs == 5
    assert stats.warmup_runs == 2
    assert stats.total_ms >= 0
    assert stats.min_ms <= stats.median_ms <= stats.max_ms
    assert stats.p95_ms <= stats.max_ms
    assert stats.p99_ms <= stats.max_ms
    assert stats.standard_deviation_ms >= 0
    assert stats.coefficient_of_variation >= 0
    assert len(stats.samples_sha256) == 64
    assert stats.inferences_per_second > 0
