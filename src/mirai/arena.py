"""Relatório portátil para benchmarks comparáveis e submissão voluntária futura."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from . import __version__
from .attachments import ATTACHMENT_EXTENSIONS
from .benchmark import BenchmarkStats, benchmark_model
from .errors import MiraiRuntimeError
from .hardware import collect_hardware_identity
from .json_codec import canonical_json_bytes, strict_json_dumps, strict_json_loads
from .providers import normalize_provider_profile, resolve_provider_profile
from .runtime import load_runtime_dependencies
from .signing import sign_artifact
from .storage import atomic_write_text, stable_file_digest

ARENA_RESULT_FORMAT = "mirai.arena-result"
ARENA_RESULT_VERSION = 1
ARENA_SUITE = "mirai-edge-v1"
MAX_ARENA_RESULT_BYTES = 1024 * 1024
ARENA_CATEGORIES = {
    "sbc-arm",
    "mini-pc",
    "jetson",
    "desktop",
    "server",
    "experimental",
    "unclassified",
}


@dataclass(frozen=True, slots=True)
class ArenaOutcome:
    report_path: Path
    signature_path: Path | None
    report: dict[str, Any]


def _safe_metrics(stats: BenchmarkStats) -> dict[str, Any]:
    return {
        key: None if isinstance(value, float) and not math.isfinite(value) else value
        for key, value in asdict(stats).items()
    }


def _split_spec(spec: str) -> tuple[str | None, str]:
    possible_name, separator, value = spec.partition("=")
    return (possible_name, value) if separator else (None, spec)


def describe_inputs(input_specs: list[str] | None) -> dict[str, Any]:
    """Descreve fixtures por digest, sem publicar valor literal ou caminho."""
    descriptors: list[dict[str, Any]] = []
    for index, spec in enumerate(input_specs or []):
        name, raw_value = _split_spec(spec)
        candidate = Path(raw_value).expanduser()
        suffix = candidate.suffix.lower()
        if suffix in ATTACHMENT_EXTENSIONS and candidate.is_file():
            digest, size = stable_file_digest(candidate)
            descriptors.append(
                {
                    "index": index,
                    "name": name,
                    "kind": "file",
                    "media_type": ATTACHMENT_EXTENSIONS[suffix],
                    "size_bytes": size,
                    "sha256": digest,
                }
            )
        else:
            encoded = raw_value.encode("utf-8")
            descriptors.append(
                {
                    "index": index,
                    "name": name,
                    "kind": "literal",
                    "media_type": "application/json-or-scalar",
                    "size_bytes": len(encoded),
                    "sha256": hashlib.sha256(encoded).hexdigest(),
                }
            )
    return {
        "count": len(descriptors),
        "uses_runtime_default": not input_specs,
        "values_included": False,
        "items": descriptors,
    }


def _normalized_category(category: str | None) -> str:
    normalized = (category or "unclassified").strip().lower()
    if normalized not in ARENA_CATEGORIES:
        raise MiraiRuntimeError(
            "categoria da Arena desconhecida; use: "
            + ", ".join(sorted(ARENA_CATEGORIES))
        )
    return normalized


def create_arena_result(
    artifact_path: Path,
    output_path: Path,
    *,
    runs: int,
    warmup_runs: int,
    input_specs: list[str] | None = None,
    layout: str = "auto",
    category: str | None = None,
    precision: str = "unknown",
    provider_profile: str = "auto",
    signing_key_path: Path | None = None,
    replace: bool = False,
) -> ArenaOutcome:
    """Executa a suíte single-stream e grava um resultado privacy-safe."""
    artifact = artifact_path.expanduser().resolve()
    output = output_path.expanduser().resolve()
    if output.suffix.lower() != ".json":
        raise MiraiRuntimeError("o resultado da Arena deve usar a extensão .json")
    if output.exists() and not replace:
        raise MiraiRuntimeError(f"arquivo de saída já existe: {output}")
    if runs <= 0 or warmup_runs < 0:
        raise MiraiRuntimeError("runs deve ser positivo e warmup não negativo")
    normalized_precision = precision.strip().lower()
    if not normalized_precision or len(normalized_precision) > 32:
        raise MiraiRuntimeError("precisão do modelo inválida")
    normalized_category = _normalized_category(category)
    normalized_provider = normalize_provider_profile(provider_profile)
    input_descriptor = describe_inputs(input_specs)
    artifact_digest, artifact_size = stable_file_digest(artifact)
    workload_subject = {
        "artifact_sha256": artifact_digest,
        "layout": layout,
        "inputs": input_descriptor,
    }
    workload_id = "workload-" + hashlib.sha256(
        canonical_json_bytes(workload_subject)
    ).hexdigest()[:24]

    ort, _ = load_runtime_dependencies()
    available_providers = list(ort.get_available_providers())
    selected_providers = resolve_provider_profile(
        normalized_provider,
        available_providers,
    )
    hardware = collect_hardware_identity(
        providers=selected_providers,
        category=normalized_category if category else None,
    )
    stats = benchmark_model(
        artifact,
        runs,
        warmup_runs,
        input_specs,
        layout,
        normalized_provider,
    )
    report: dict[str, Any] = {
        "format": ARENA_RESULT_FORMAT,
        "schema_version": ARENA_RESULT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "suite": {
            "name": ARENA_SUITE,
            "version": 1,
            "scenario": "single-stream",
            "workload_id": workload_id,
            "category": normalized_category,
        },
        "subject": {
            "name": artifact.name,
            "sha256": artifact_digest,
            "size_bytes": artifact_size,
            "precision": normalized_precision,
            "precision_source": "user" if normalized_precision != "unknown" else "unknown",
        },
        "inputs": input_descriptor,
        "hardware": hardware,
        "runtime": {
            "name": "onnxruntime",
            "version": str(getattr(ort, "__version__", "unknown")),
            "available_providers": available_providers,
            "selected_providers": selected_providers,
            "provider_profile": normalized_provider,
            "miraios_version": __version__,
        },
        "metrics": _safe_metrics(stats),
        "verification": {
            "level": "self-measured",
            "signature": "detached-optional",
            "raw_samples_included": False,
            "samples_sha256": stats.samples_sha256,
        },
        "privacy": {
            "raw_inputs_included": False,
            "hostname_included": False,
            "network_identifiers_included": False,
        },
        "limitations": [
            "resultado mede somente o workload identificado por hash",
            "não equivale a uma submissão MLPerf",
            "energia e memória nativa ainda não são medidas pela suíte v1",
            "assinatura prova a chave, não a veracidade física do hardware",
            "digests de entradas literais de baixa entropia podem ser adivinhados",
        ],
    }
    report["result_id"] = "result-" + hashlib.sha256(
        canonical_json_bytes(report)
    ).hexdigest()[:24]
    try:
        atomic_write_text(
            output,
            strict_json_dumps(report, pretty=True, sort_keys=True) + "\n",
            mode=0o600,
        )
    except OSError as error:
        raise MiraiRuntimeError(
            f"não foi possível gravar o resultado da Arena: {error}"
        ) from error
    signature_path: Path | None = None
    if signing_key_path is not None:
        signed = sign_artifact(
            output,
            signing_key_path,
            replace=replace,
        )
        signature_path = Path(signed["signature"])
    return ArenaOutcome(output, signature_path, report)


def load_arena_result(path: Path) -> dict[str, Any]:
    target = path.expanduser().resolve()
    try:
        size = target.stat().st_size
        content = target.read_bytes()
    except OSError as error:
        raise MiraiRuntimeError(f"não foi possível ler o resultado da Arena: {error}") from error
    if not 0 < size <= MAX_ARENA_RESULT_BYTES:
        raise MiraiRuntimeError("resultado da Arena vazio ou acima de 1 MB")
    report = strict_json_loads(content, label="resultado da Arena")
    if not isinstance(report, dict):
        raise MiraiRuntimeError("resultado da Arena deve ser um objeto JSON")
    required = {
        "format",
        "schema_version",
        "created_at",
        "suite",
        "subject",
        "inputs",
        "hardware",
        "runtime",
        "metrics",
        "verification",
        "privacy",
        "limitations",
        "result_id",
    }
    if set(report) != required:
        raise MiraiRuntimeError("resultado da Arena possui campos incompatíveis")
    if (
        report["format"] != ARENA_RESULT_FORMAT
        or report["schema_version"] != ARENA_RESULT_VERSION
    ):
        raise MiraiRuntimeError("versão do resultado da Arena não suportada")
    subject = report.get("subject")
    metrics = report.get("metrics")
    hardware = report.get("hardware")
    if not isinstance(subject, dict) or not isinstance(metrics, dict):
        raise MiraiRuntimeError("subject ou métricas da Arena inválidos")
    if set(subject) != {
        "name",
        "sha256",
        "size_bytes",
        "precision",
        "precision_source",
    }:
        raise MiraiRuntimeError("subject da Arena possui campos incompatíveis")
    subject_digest = subject.get("sha256")
    if (
        not isinstance(subject.get("name"), str)
        or Path(subject["name"]).name != subject["name"]
        or not isinstance(subject_digest, str)
        or len(subject_digest) != 64
        or any(character not in "0123456789abcdef" for character in subject_digest)
        or isinstance(subject.get("size_bytes"), bool)
        or not isinstance(subject.get("size_bytes"), int)
        or subject["size_bytes"] <= 0
    ):
        raise MiraiRuntimeError("identidade do subject da Arena inválida")
    if not isinstance(hardware, dict) or not isinstance(hardware.get("hardware_id"), str):
        raise MiraiRuntimeError("identidade de hardware da Arena inválida")
    suite = report.get("suite")
    if (
        not isinstance(suite, dict)
        or suite.get("name") != ARENA_SUITE
        or suite.get("version") != 1
        or suite.get("scenario") != "single-stream"
        or suite.get("category") not in ARENA_CATEGORIES
        or not isinstance(suite.get("workload_id"), str)
        or not suite["workload_id"].startswith("workload-")
    ):
        raise MiraiRuntimeError("suite da Arena inválida")
    if hardware.get("category") != suite["category"]:
        raise MiraiRuntimeError("categoria do hardware não corresponde à suite")
    runtime = report.get("runtime")
    if not isinstance(runtime, dict) or set(runtime) != {
        "name",
        "version",
        "available_providers",
        "selected_providers",
        "provider_profile",
        "miraios_version",
    }:
        raise MiraiRuntimeError("runtime da Arena inválido")
    if (
        runtime.get("name") != "onnxruntime"
        or not isinstance(runtime.get("available_providers"), list)
        or not isinstance(runtime.get("selected_providers"), list)
        or not runtime["selected_providers"]
        or any(not isinstance(item, str) for item in runtime["available_providers"])
        or any(not isinstance(item, str) for item in runtime["selected_providers"])
        or not set(runtime["selected_providers"]).issubset(runtime["available_providers"])
    ):
        raise MiraiRuntimeError("providers do resultado da Arena inválidos")
    provider_profile = runtime.get("provider_profile")
    if not isinstance(provider_profile, str):
        raise MiraiRuntimeError("provider profile do resultado da Arena inválido")
    normalize_provider_profile(provider_profile)
    verification = report.get("verification")
    if not isinstance(verification, dict) or set(verification) != {
        "level",
        "signature",
        "raw_samples_included",
        "samples_sha256",
    }:
        raise MiraiRuntimeError("verificação do resultado da Arena inválida")
    if (
        verification.get("level") != "self-measured"
        or verification.get("signature") != "detached-optional"
        or verification.get("raw_samples_included") is not False
        or verification.get("samples_sha256") != metrics.get("samples_sha256")
    ):
        raise MiraiRuntimeError("claims de verificação da Arena inválidos")
    expected_metrics = {
        "runs",
        "warmup_runs",
        "total_ms",
        "average_ms",
        "median_ms",
        "p95_ms",
        "min_ms",
        "max_ms",
        "inferences_per_second",
        "p99_ms",
        "standard_deviation_ms",
        "coefficient_of_variation",
        "samples_sha256",
    }
    if set(metrics) != expected_metrics:
        raise MiraiRuntimeError("campos de métricas da Arena incompatíveis")
    if (
        isinstance(metrics.get("runs"), bool)
        or not isinstance(metrics.get("runs"), int)
        or metrics["runs"] <= 0
        or isinstance(metrics.get("warmup_runs"), bool)
        or not isinstance(metrics.get("warmup_runs"), int)
        or metrics["warmup_runs"] < 0
    ):
        raise MiraiRuntimeError("contadores de benchmark da Arena inválidos")
    numeric_fields = (
        "total_ms",
        "average_ms",
        "median_ms",
        "p95_ms",
        "min_ms",
        "max_ms",
        "p99_ms",
        "standard_deviation_ms",
        "coefficient_of_variation",
    )
    for field in numeric_fields:
        value = metrics.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise MiraiRuntimeError(f"métrica da Arena inválida: {field}")
    ips = metrics.get("inferences_per_second")
    if ips is not None and (
        isinstance(ips, bool)
        or not isinstance(ips, (int, float))
        or not math.isfinite(ips)
        or ips <= 0
    ):
        raise MiraiRuntimeError("métrica da Arena inválida: inferences_per_second")
    sample_digest = metrics.get("samples_sha256")
    if (
        not isinstance(sample_digest, str)
        or len(sample_digest) != 64
        or any(character not in "0123456789abcdef" for character in sample_digest)
    ):
        raise MiraiRuntimeError("digest das amostras da Arena inválido")
    if not (
        metrics["min_ms"]
        <= metrics["median_ms"]
        <= metrics["p95_ms"]
        <= metrics["p99_ms"]
        <= metrics["max_ms"]
    ):
        raise MiraiRuntimeError("ordem das latências da Arena inválida")
    claimed_id = report.get("result_id")
    without_id = dict(report)
    del without_id["result_id"]
    expected_id = "result-" + hashlib.sha256(
        canonical_json_bytes(without_id)
    ).hexdigest()[:24]
    if claimed_id != expected_id:
        raise MiraiRuntimeError("identificador do resultado da Arena não confere")
    return report


def verify_arena_result(
    path: Path,
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    report = load_arena_result(path)
    if artifact_path is not None:
        artifact = artifact_path.expanduser().resolve()
        digest, size = stable_file_digest(artifact)
        if report["subject"].get("name") != artifact.name:
            raise MiraiRuntimeError("nome do artefato não corresponde ao resultado")
        if report["subject"].get("sha256") != digest:
            raise MiraiRuntimeError("SHA-256 do artefato não corresponde ao resultado")
        if report["subject"].get("size_bytes") != size:
            raise MiraiRuntimeError("tamanho do artefato não corresponde ao resultado")
    return report


def calculate_edge_score(
    report: dict[str, Any],
    *,
    target_p95_ms: float,
    target_ips: float,
    max_coefficient_of_variation: float = 0.25,
) -> dict[str, Any]:
    """Calcula conformidade com um SLA; não inventa um ranking universal."""
    values = (target_p95_ms, target_ips, max_coefficient_of_variation)
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise MiraiRuntimeError("metas do Edge Score devem ser finitas e positivas")
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise MiraiRuntimeError("resultado não possui métricas para o Edge Score")
    p95 = metrics.get("p95_ms")
    ips = metrics.get("inferences_per_second")
    variation = metrics.get("coefficient_of_variation")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in (p95, ips, variation)
    ):
        raise MiraiRuntimeError("métricas insuficientes para o Edge Score")
    p95_value = float(cast(float, p95))
    ips_value = float(cast(float, ips))
    variation_value = float(cast(float, variation))
    if (
        not all(math.isfinite(value) for value in (p95_value, ips_value, variation_value))
        or p95_value < 0
        or ips_value < 0
        or variation_value < 0
    ):
        raise MiraiRuntimeError("métricas inválidas para o Edge Score")
    latency_component = (
        1.0 if p95_value == 0 else min(1.0, target_p95_ms / p95_value)
    )
    throughput_component = min(1.0, ips_value / target_ips)
    stability_component = (
        1.0
        if variation_value <= max_coefficient_of_variation
        else max_coefficient_of_variation / variation_value
    )
    compliance = min(latency_component, throughput_component, stability_component)
    score = round(100.0 * compliance, 2)
    grade = (
        "A"
        if score == 100
        else "B"
        if score >= 90
        else "C"
        if score >= 75
        else "D"
        if score >= 50
        else "E"
    )
    return {
        "schema_version": 1,
        "kind": "sla-conformance",
        "score": score,
        "grade": grade,
        "passed": compliance >= 1.0,
        "components": {
            "latency": round(latency_component * 100, 2),
            "throughput": round(throughput_component * 100, 2),
            "stability": round(stability_component * 100, 2),
        },
        "policy": {
            "target_p95_ms": target_p95_ms,
            "target_ips": target_ips,
            "max_coefficient_of_variation": max_coefficient_of_variation,
        },
        "result_id": report.get("result_id"),
        "warning": "não compare scores produzidos com políticas ou workloads diferentes",
    }
