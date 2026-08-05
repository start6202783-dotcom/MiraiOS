"""Testes de métricas persistentes, privacidade e drift da v0.13."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import mirai.observability as observability_module
from mirai.errors import MiraiRuntimeError
from mirai.observability import MAX_OUTPUT_VALUES, MAX_SAMPLES, ObservabilityStore

DEPLOYMENT = "a" * 16
AGENT = "b" * 32


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_observability_counts_and_summarizes_without_raw_output(tmp_path: Path) -> None:
    path = tmp_path / "observability.json"
    store = ObservabilityStore(path, flush_every=1)

    store.record_deployment(DEPLOYMENT)
    store.record_activation(DEPLOYMENT)
    store.record_inference(
        DEPLOYMENT,
        latency_ms=4.5,
        result={"prediction": [1.0, 3.0], "private": "never-persist-this"},
    )
    store.record_inference(DEPLOYMENT, failed=True)
    snapshot = store.snapshot()
    persisted = path.read_text(encoding="utf-8")

    assert snapshot["counters"] == {
        "deployments_total": 1,
        "activations_total": 1,
        "inferences_total": 2,
        "inference_failures_total": 1,
    }
    assert snapshot["deployments"][DEPLOYMENT]["error_rate"] == 0.5
    assert snapshot["deployments"][DEPLOYMENT]["latency"]["p95_ms"] == 4.5
    assert "never-persist-this" not in persisted
    assert "prediction" not in persisted
    assert json.loads(persisted)["deployments"][DEPLOYMENT]["output_signal"] == [2.0]


def test_inference_persistence_is_batched_and_flushable(tmp_path: Path) -> None:
    path = tmp_path / "observability.json"
    store = ObservabilityStore(path, flush_every=3)
    store.record_deployment(DEPLOYMENT)
    before = path.read_text(encoding="utf-8")

    store.record_inference(DEPLOYMENT, latency_ms=1.0, result=[1.0])

    assert path.read_text(encoding="utf-8") == before
    assert store.snapshot()["persistence"]["pending_inferences"] == 1
    assert store.flush() is True
    assert json.loads(path.read_text(encoding="utf-8"))["counters"]["inferences_total"] == 1
    assert store.snapshot()["persistence"]["pending_inferences"] == 0


def test_inference_flushes_after_interval(tmp_path: Path) -> None:
    clock = FakeClock()
    path = tmp_path / "observability.json"
    store = ObservabilityStore(
        path,
        clock=clock,
        flush_every=10,
        flush_interval_seconds=5,
    )
    store.record_deployment(DEPLOYMENT)
    clock.advance(6)

    store.record_inference(DEPLOYMENT, latency_ms=1.0)

    assert json.loads(path.read_text(encoding="utf-8"))["counters"]["inferences_total"] == 1


def test_runtime_persistence_failure_degrades_metrics_not_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ObservabilityStore(tmp_path / "observability.json", flush_every=1)

    def fail(*_: Any, **__: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(observability_module, "atomic_write_text", fail)

    store.record_deployment(DEPLOYMENT)
    store.record_inference(DEPLOYMENT, latency_ms=2.0)

    snapshot = store.snapshot()
    assert snapshot["counters"]["inferences_total"] == 1
    assert snapshot["persistence"]["status"] == "degraded"


def test_flush_recovers_from_transient_persistence_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ObservabilityStore(tmp_path / "observability.json", flush_every=1)
    original = observability_module.atomic_write_text
    monkeypatch.setattr(
        observability_module,
        "atomic_write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("temporary")),
    )
    store.record_inference(DEPLOYMENT, latency_ms=2.0)
    monkeypatch.setattr(observability_module, "atomic_write_text", original)

    assert store.flush() is True
    assert store.snapshot()["persistence"]["status"] == "healthy"


def test_observability_survives_restart_after_flush(tmp_path: Path) -> None:
    path = tmp_path / "observability.json"
    first = ObservabilityStore(path, flush_every=100)
    first.record_deployment(DEPLOYMENT)
    first.record_inference(DEPLOYMENT, latency_ms=2.0, result=[4.0])
    first.flush()

    second = ObservabilityStore(path)
    snapshot = second.snapshot()

    assert snapshot["counters"]["deployments_total"] == 1
    assert snapshot["counters"]["inferences_total"] == 1
    assert snapshot["deployments"][DEPLOYMENT]["latency"]["median_ms"] == 2.0


def test_drift_requires_two_windows_and_detects_mean_shift(tmp_path: Path) -> None:
    stable = ObservabilityStore(tmp_path / "stable.json", flush_every=100)
    warning = ObservabilityStore(tmp_path / "warning.json", flush_every=100)
    stable.record_deployment(DEPLOYMENT)
    warning.record_deployment(DEPLOYMENT)
    for _ in range(20):
        stable.record_inference(DEPLOYMENT, latency_ms=10.0, result=[5.0])
        warning.record_inference(DEPLOYMENT, latency_ms=10.0, result=[1.0])
    assert (
        stable.snapshot()["deployments"][DEPLOYMENT]["drift"]["output"]["status"]
        == "insufficient_data"
    )
    for _ in range(20):
        stable.record_inference(DEPLOYMENT, latency_ms=10.0, result=[5.0])
        warning.record_inference(DEPLOYMENT, latency_ms=20.0, result=[3.0])

    stable_drift = stable.snapshot()["deployments"][DEPLOYMENT]["drift"]
    warning_drift = warning.snapshot()["deployments"][DEPLOYMENT]["drift"]
    assert stable_drift["latency"]["status"] == "stable"
    assert stable_drift["output"]["status"] == "stable"
    assert warning_drift["latency"]["status"] == "warning"
    assert warning_drift["output"]["status"] == "warning"


def test_observability_retains_bounded_samples(tmp_path: Path) -> None:
    store = ObservabilityStore(tmp_path / "observability.json", flush_every=MAX_SAMPLES)
    store.record_deployment(DEPLOYMENT)

    for index in range(MAX_SAMPLES + 25):
        store.record_inference(DEPLOYMENT, latency_ms=float(index), result=[float(index)])
    store.flush()
    persisted = json.loads((tmp_path / "observability.json").read_text(encoding="utf-8"))
    deployment = persisted["deployments"][DEPLOYMENT]

    assert len(deployment["latency_ms"]) == MAX_SAMPLES
    assert len(deployment["output_signal"]) == MAX_SAMPLES
    assert deployment["latency_ms"][0] == 25.0


def test_oversized_output_is_not_traversed_or_persisted(tmp_path: Path) -> None:
    store = ObservabilityStore(tmp_path / "observability.json", flush_every=1)
    store.record_deployment(DEPLOYMENT)

    store.record_inference(DEPLOYMENT, result=list(range(MAX_OUTPUT_VALUES + 1)))

    persisted = json.loads((tmp_path / "observability.json").read_text(encoding="utf-8"))
    assert persisted["deployments"][DEPLOYMENT]["output_signal"] == []


def test_prometheus_output_has_controlled_labels_and_numeric_metrics(tmp_path: Path) -> None:
    store = ObservabilityStore(tmp_path / "observability.json", flush_every=1)
    store.record_deployment(DEPLOYMENT)
    store.record_inference(DEPLOYMENT, latency_ms=3.0)

    output = store.prometheus(agent_id=AGENT)

    assert f'agent_id="{AGENT}"' in output
    assert f'deployment_id="{DEPLOYMENT}"' in output
    assert "mirai_agent_inferences_total" in output
    assert "mirai_deployment_latency_p95_ms" in output
    with pytest.raises(MiraiRuntimeError, match="agent_id"):
        store.prometheus(agent_id='bad"label')


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {
            "version": 99,
            "created_at": "x",
            "updated_at": "x",
            "counters": {},
            "deployments": {},
        },
        {
            "version": 1,
            "created_at": "x",
            "updated_at": "x",
            "counters": {
                "deployments_total": 0,
                "activations_total": 0,
                "inferences_total": True,
                "inference_failures_total": 0,
            },
            "deployments": {},
        },
        {
            "version": 1,
            "created_at": "x",
            "updated_at": "x",
            "counters": {
                "deployments_total": 0,
                "activations_total": 0,
                "inferences_total": 1,
                "inference_failures_total": 0,
            },
            "deployments": {},
        },
        {
            "version": 1,
            "created_at": "x",
            "updated_at": "x",
            "counters": {
                "deployments_total": 0,
                "activations_total": 0,
                "inferences_total": 0,
                "inference_failures_total": 0,
            },
            "deployments": {
                DEPLOYMENT: {
                    "inferences_total": 0,
                    "failures_total": 1,
                    "latency_ms": [],
                    "output_signal": [],
                }
            },
        },
    ],
)
def test_corrupted_observability_state_fails_closed(
    tmp_path: Path,
    payload: dict[str, Any],
) -> None:
    path = tmp_path / "observability.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MiraiRuntimeError):
        ObservabilityStore(path)


def test_invalid_json_observability_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "observability.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="ler a observabilidade"):
        ObservabilityStore(path)


@pytest.mark.parametrize(
    ("flush_every", "interval"),
    [(0, 5.0), (MAX_SAMPLES + 1, 5.0), (1, 0.0), (1, float("inf"))],
)
def test_observability_rejects_invalid_flush_policy(
    tmp_path: Path,
    flush_every: int,
    interval: float,
) -> None:
    with pytest.raises(MiraiRuntimeError):
        ObservabilityStore(
            tmp_path / "observability.json",
            flush_every=flush_every,
            flush_interval_seconds=interval,
        )


def test_observability_rejects_untrusted_deployment_label(tmp_path: Path) -> None:
    store = ObservabilityStore(tmp_path / "observability.json")

    with pytest.raises(MiraiRuntimeError, match="deployment_id"):
        store.record_inference('bad"label', latency_ms=1.0)


def test_observability_enforces_deployment_capacity(tmp_path: Path) -> None:
    store = ObservabilityStore(tmp_path / "observability.json")
    empty = {
        "inferences_total": 0,
        "failures_total": 0,
        "latency_ms": [],
        "output_signal": [],
    }
    store._state["deployments"] = {f"{index:016x}": dict(empty) for index in range(1024)}

    with pytest.raises(MiraiRuntimeError, match="limite"):
        store.record_deployment("f" * 16)


def test_observability_detects_in_memory_corruption(tmp_path: Path) -> None:
    store = ObservabilityStore(tmp_path / "observability.json")
    store._state["deployments"][DEPLOYMENT] = "corrupt"

    with pytest.raises(MiraiRuntimeError, match="corrompido"):
        store.record_activation(DEPLOYMENT)


def test_prometheus_exports_drift_scores_after_two_windows(tmp_path: Path) -> None:
    store = ObservabilityStore(tmp_path / "observability.json", flush_every=100)
    store.record_deployment(DEPLOYMENT)
    for index in range(40):
        store.record_inference(
            DEPLOYMENT,
            latency_ms=1.0 if index < 20 else 3.0,
            result=[1.0 if index < 20 else 4.0],
        )

    output = store.prometheus(agent_id=AGENT)

    assert "mirai_deployment_latency_drift_score" in output
    assert "mirai_deployment_output_drift_score" in output
