"""Métricas locais e sinais conservadores de drift sem persistir entradas."""

from __future__ import annotations

import math
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, pstdev
from time import monotonic
from typing import Any

from .errors import MiraiRuntimeError
from .json_codec import strict_json_dumps, strict_json_loads
from .storage import atomic_write_text

OBSERVABILITY_VERSION = 1
MAX_DEPLOYMENTS = 1_024
MAX_SAMPLES = 200
DRIFT_WINDOW = 20
MAX_OUTPUT_VALUES = 10_000
DRIFT_WARNING_SCORE = 0.50
DEFAULT_FLUSH_EVERY = 10
DEFAULT_FLUSH_INTERVAL_SECONDS = 5.0
DEPLOYMENT_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
AGENT_ID_PATTERN = re.compile(r"^(?:[0-9a-f]{32}|unpaired-local)$")
COUNTER_NAMES = {
    "deployments_total",
    "activations_total",
    "inferences_total",
    "inference_failures_total",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _numeric_signal(value: Any) -> float | None:
    """Resume uma saída numérica sem guardar seus valores individuais."""
    pending = [value]
    numbers: list[float] = []
    visited = 0
    while pending:
        current = pending.pop()
        visited += 1
        if visited > MAX_OUTPUT_VALUES:
            return None
        if isinstance(current, bool):
            continue
        if isinstance(current, (int, float)):
            number = float(current)
            if math.isfinite(number):
                numbers.append(number)
            continue
        if isinstance(current, (list, tuple)):
            pending.extend(current)
            continue
        if isinstance(current, dict):
            pending.extend(current.values())
    return mean(numbers) if numbers else None


def _drift_signal(samples: list[float]) -> dict[str, Any]:
    required = DRIFT_WINDOW * 2
    if len(samples) < required:
        return {
            "status": "insufficient_data",
            "samples": len(samples),
            "required": required,
            "score": None,
        }
    baseline = samples[:DRIFT_WINDOW]
    current = samples[-DRIFT_WINDOW:]
    baseline_mean = mean(baseline)
    current_mean = mean(current)
    scale = max(abs(baseline_mean), pstdev(baseline), 1e-9)
    score = abs(current_mean - baseline_mean) / scale
    return {
        "status": "warning" if score >= DRIFT_WARNING_SCORE else "stable",
        "samples": len(samples),
        "required": required,
        "score": score,
        "threshold": DRIFT_WARNING_SCORE,
        "baseline_mean": baseline_mean,
        "current_mean": current_mean,
        "method": "normalized_mean_shift",
    }


class ObservabilityStore:
    """Estado pequeno, persistente e thread-safe da operação do Agent."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Any = monotonic,
        flush_every: int = DEFAULT_FLUSH_EVERY,
        flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
    ) -> None:
        if not 1 <= flush_every <= MAX_SAMPLES:
            raise MiraiRuntimeError(f"flush_every deve estar entre 1 e {MAX_SAMPLES}")
        if not math.isfinite(flush_interval_seconds) or flush_interval_seconds <= 0:
            raise MiraiRuntimeError("flush_interval_seconds deve ser finito e positivo")
        self.path = path
        self._clock = clock
        self._started_monotonic = float(clock())
        self._process_started_at = _utc_now()
        self._last_flush_monotonic = self._started_monotonic
        self._flush_every = flush_every
        self._flush_interval_seconds = flush_interval_seconds
        self._pending_inferences = 0
        self._persistence_degraded = False
        self._lock = threading.Lock()
        self._state = self._load()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {
            "version": OBSERVABILITY_VERSION,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "counters": {
                "deployments_total": 0,
                "activations_total": 0,
                "inferences_total": 0,
                "inference_failures_total": 0,
            },
            "deployments": {},
        }

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._empty()
        try:
            payload = strict_json_loads(
                self.path.read_bytes(),
                label="estado de observabilidade",
            )
        except (OSError, MiraiRuntimeError) as error:
            raise MiraiRuntimeError(f"não foi possível ler a observabilidade: {error}") from error
        if (
            not isinstance(payload, dict)
            or set(payload)
            != {"version", "created_at", "updated_at", "counters", "deployments"}
            or payload.get("version") != OBSERVABILITY_VERSION
            or not isinstance(payload.get("created_at"), str)
            or not isinstance(payload.get("updated_at"), str)
            or not isinstance(payload.get("counters"), dict)
            or not isinstance(payload.get("deployments"), dict)
            or len(payload["deployments"]) > MAX_DEPLOYMENTS
        ):
            raise MiraiRuntimeError("estado de observabilidade incompatível")
        counters = payload["counters"]
        if set(counters) != COUNTER_NAMES or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counters.values()
        ):
            raise MiraiRuntimeError("contadores de observabilidade estão corrompidos")

        inference_total = 0
        failure_total = 0
        for deployment_id, deployment in payload["deployments"].items():
            if (
                not isinstance(deployment_id, str)
                or not DEPLOYMENT_ID_PATTERN.fullmatch(deployment_id)
                or not isinstance(deployment, dict)
                or set(deployment)
                != {"inferences_total", "failures_total", "latency_ms", "output_signal"}
            ):
                raise MiraiRuntimeError("deployment de observabilidade está corrompido")
            total = deployment["inferences_total"]
            failures = deployment["failures_total"]
            latencies = deployment["latency_ms"]
            outputs = deployment["output_signal"]
            if (
                isinstance(total, bool)
                or not isinstance(total, int)
                or total < 0
                or isinstance(failures, bool)
                or not isinstance(failures, int)
                or failures < 0
                or failures > total
                or not isinstance(latencies, list)
                or not isinstance(outputs, list)
                or len(latencies) > MAX_SAMPLES
                or len(outputs) > MAX_SAMPLES
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or float(value) < 0
                    for value in latencies
                )
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in outputs
                )
            ):
                raise MiraiRuntimeError("amostras de observabilidade estão corrompidas")
            inference_total += total
            failure_total += failures
        if (
            counters["inferences_total"] != inference_total
            or counters["inference_failures_total"] != failure_total
        ):
            raise MiraiRuntimeError("totais de observabilidade não conferem")
        return payload

    def _save_unlocked(self) -> None:
        self._state["updated_at"] = _utc_now()
        try:
            atomic_write_text(
                self.path,
                strict_json_dumps(self._state, pretty=True) + "\n",
                mode=0o600,
            )
        except OSError as error:
            raise MiraiRuntimeError(
                f"não foi possível salvar a observabilidade: {error}"
            ) from error

    def _try_save_unlocked(self) -> bool:
        """Persiste sem permitir que telemetria derrube a inferência principal."""
        try:
            self._save_unlocked()
        except MiraiRuntimeError:
            self._persistence_degraded = True
            return False
        self._persistence_degraded = False
        self._pending_inferences = 0
        self._last_flush_monotonic = float(self._clock())
        return True

    def _deployment_unlocked(self, deployment_id: str) -> dict[str, Any]:
        if not DEPLOYMENT_ID_PATTERN.fullmatch(deployment_id):
            raise MiraiRuntimeError("deployment_id de observabilidade é inválido")
        deployments = self._state["deployments"]
        if deployment_id not in deployments:
            if len(deployments) >= MAX_DEPLOYMENTS:
                raise MiraiRuntimeError("observabilidade atingiu o limite de deployments")
            deployments[deployment_id] = {
                "inferences_total": 0,
                "failures_total": 0,
                "latency_ms": [],
                "output_signal": [],
            }
        deployment = deployments[deployment_id]
        if not isinstance(deployment, dict):
            raise MiraiRuntimeError("estado de observabilidade corrompido")
        return deployment

    def record_deployment(self, deployment_id: str) -> None:
        with self._lock:
            self._deployment_unlocked(deployment_id)
            self._state["counters"]["deployments_total"] += 1
            self._try_save_unlocked()

    def record_activation(self, deployment_id: str) -> None:
        with self._lock:
            self._deployment_unlocked(deployment_id)
            self._state["counters"]["activations_total"] += 1
            self._try_save_unlocked()

    def record_inference(
        self,
        deployment_id: str,
        *,
        latency_ms: float | None = None,
        result: Any = None,
        failed: bool = False,
    ) -> None:
        with self._lock:
            deployment = self._deployment_unlocked(deployment_id)
            self._state["counters"]["inferences_total"] += 1
            deployment["inferences_total"] += 1
            if failed:
                self._state["counters"]["inference_failures_total"] += 1
                deployment["failures_total"] += 1
            if latency_ms is not None:
                latency = float(latency_ms)
                if math.isfinite(latency) and latency >= 0:
                    deployment["latency_ms"] = [
                        *deployment["latency_ms"],
                        latency,
                    ][-MAX_SAMPLES:]
            signal = _numeric_signal(result)
            if signal is not None:
                deployment["output_signal"] = [
                    *deployment["output_signal"],
                    signal,
                ][-MAX_SAMPLES:]
            self._pending_inferences += 1
            now = float(self._clock())
            if (
                self._pending_inferences >= self._flush_every
                or now - self._last_flush_monotonic >= self._flush_interval_seconds
            ):
                self._try_save_unlocked()

    def flush(self) -> bool:
        """Força a persistência dos contadores pendentes no encerramento limpo."""
        with self._lock:
            if self._pending_inferences == 0 and not self._persistence_degraded:
                return True
            return self._try_save_unlocked()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self._state["counters"])
            deployments: dict[str, Any] = {}
            for deployment_id, raw in self._state["deployments"].items():
                latencies = [float(value) for value in raw.get("latency_ms", [])]
                output_samples = [float(value) for value in raw.get("output_signal", [])]
                total = int(raw.get("inferences_total", 0))
                failures = int(raw.get("failures_total", 0))
                deployments[deployment_id] = {
                    "inferences_total": total,
                    "failures_total": failures,
                    "error_rate": failures / total if total else 0.0,
                    "latency": {
                        "samples": len(latencies),
                        "median_ms": median(latencies) if latencies else None,
                        "p95_ms": _percentile(latencies, 0.95),
                    },
                    "drift": {
                        "latency": _drift_signal(latencies),
                        "output": _drift_signal(output_samples),
                        "interpretation": ("sinal heurístico; não comprova concept drift"),
                    },
                }
            return {
                "version": OBSERVABILITY_VERSION,
                "started_at": self._process_started_at,
                "updated_at": self._state["updated_at"],
                "uptime_seconds": max(0.0, float(self._clock()) - self._started_monotonic),
                "persistence": {
                    "status": "degraded" if self._persistence_degraded else "healthy",
                    "pending_inferences": self._pending_inferences,
                    "flush_every": self._flush_every,
                },
                "counters": counters,
                "deployments": deployments,
            }

    def prometheus(self, *, agent_id: str) -> str:
        """Renderiza somente métricas numéricas com labels controlados."""
        if not AGENT_ID_PATTERN.fullmatch(agent_id):
            raise MiraiRuntimeError("agent_id de observabilidade é inválido")
        snapshot = self.snapshot()
        lines = [
            "# HELP mirai_agent_uptime_seconds Tempo desde o início do Agent.",
            "# TYPE mirai_agent_uptime_seconds gauge",
            (
                f'mirai_agent_uptime_seconds{{agent_id="{agent_id}"}} '
                f"{snapshot['uptime_seconds']:.6f}"
            ),
            "# TYPE mirai_observability_persistence_healthy gauge",
            (
                f'mirai_observability_persistence_healthy{{agent_id="{agent_id}"}} '
                f"{int(snapshot['persistence']['status'] == 'healthy')}"
            ),
            "# TYPE mirai_observability_pending_inferences gauge",
            (
                f'mirai_observability_pending_inferences{{agent_id="{agent_id}"}} '
                f"{snapshot['persistence']['pending_inferences']}"
            ),
        ]
        for key, value in snapshot["counters"].items():
            metric = f"mirai_agent_{key}"
            lines.extend(
                [
                    f"# TYPE {metric} counter",
                    f'{metric}{{agent_id="{agent_id}"}} {int(value)}',
                ]
            )
        for deployment_id, deployment in snapshot["deployments"].items():
            label = f'agent_id="{agent_id}",deployment_id="{deployment_id}"'
            lines.append(
                f"mirai_deployment_inferences_total{{{label}}} {deployment['inferences_total']}"
            )
            lines.append(
                f"mirai_deployment_inference_failures_total{{{label}}} "
                f"{deployment['failures_total']}"
            )
            p95 = deployment["latency"]["p95_ms"]
            if p95 is not None:
                lines.append(f"mirai_deployment_latency_p95_ms{{{label}}} {p95:.6f}")
            for drift_name in ("latency", "output"):
                drift = deployment["drift"][drift_name]
                if drift["score"] is not None:
                    lines.append(
                        f"mirai_deployment_{drift_name}_drift_score{{{label}}} {drift['score']:.6f}"
                    )
        return "\n".join(lines) + "\n"
