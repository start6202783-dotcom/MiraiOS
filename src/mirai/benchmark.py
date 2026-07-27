"""Benchmark reproduzível de modelos ONNX."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from time import perf_counter
from typing import Any

from .errors import MiraiRuntimeError
from .runtime import prepare_inference


DEFAULT_BENCHMARK_RUNS = 50
DEFAULT_WARMUP_RUNS = 3


@dataclass(frozen=True)
class BenchmarkStats:
    """Resumo das latências observadas em um benchmark."""

    runs: int
    warmup_runs: int
    total_ms: float
    average_ms: float
    median_ms: float
    p95_ms: float
    min_ms: float
    max_ms: float
    inferences_per_second: float


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def benchmark_session(
    session: Any,
    input_feed: dict[str, Any],
    runs: int,
    warmup_runs: int,
) -> BenchmarkStats:
    """Executa warm-up e mede cada inferência individualmente."""
    try:
        for _ in range(warmup_runs):
            session.run(None, input_feed)

        latencies_ms: list[float] = []
        for _ in range(runs):
            started_at = perf_counter()
            session.run(None, input_feed)
            latencies_ms.append((perf_counter() - started_at) * 1000)
    except Exception as error:
        raise MiraiRuntimeError(f"falha durante o benchmark: {error}") from error

    total_ms = sum(latencies_ms)
    return BenchmarkStats(
        runs=runs,
        warmup_runs=warmup_runs,
        total_ms=total_ms,
        average_ms=mean(latencies_ms),
        median_ms=median(latencies_ms),
        p95_ms=_percentile(latencies_ms, 0.95),
        min_ms=min(latencies_ms),
        max_ms=max(latencies_ms),
        inferences_per_second=(
            runs * 1000 / total_ms if total_ms > 0 else float("inf")
        ),
    )


def benchmark_model(
    model_path: Path,
    runs: int,
    warmup_runs: int = DEFAULT_WARMUP_RUNS,
    input_specs: list[str] | None = None,
    layout: str = "auto",
) -> BenchmarkStats:
    """Prepara um modelo e coleta estatísticas de latência."""
    session, input_feed = prepare_inference(model_path, input_specs, layout)
    return benchmark_session(session, input_feed, runs, warmup_runs)
