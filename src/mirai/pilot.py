"""Mirai Pilot: deploy transacional, critérios e evidências em um comando."""

from __future__ import annotations

import json
import math
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, TypeVar

from .agent_client import (
    activate_deployment,
    deactivate_deployment,
    deploy_model,
    doctor_device,
    get_deployment_status,
    run_remote_model,
)
from .benchmark import BenchmarkStats, summarize_latencies
from .devices import Device, get_device
from .errors import MiraiRuntimeError
from .inspect import validate_artifact
from .package import MIRAI_EXTENSION, calculate_sha256, load_mirai_package
from .providers import normalize_provider_profile
from .signing import sign_artifact

PILOT_SCHEMA_VERSION = 1
DEFAULT_PILOT_CONFIG = Path("mirai-pilot.json")
DEFAULT_REPORT_DIRECTORY = Path(".mirai") / "reports"
MAX_CONFIG_SIZE_BYTES = 256 * 1024
PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class PilotAcceptance:
    """Critérios objetivos que decidem se um piloto foi aprovado."""

    compare_result: bool = False
    expected_result: Any = None
    result_tolerance: float = 1e-6
    max_p95_ms: float | None = None
    min_ips: float | None = None


@dataclass(frozen=True, slots=True)
class PilotConfig:
    """Configuração validada de uma execução Mirai Pilot."""

    source_path: Path
    name: str
    artifact_path: Path
    device_name: str
    input_specs: list[str] | None
    layout: str
    runs: int
    warmup_runs: int
    acceptance: PilotAcceptance
    report_directory: Path
    include_inputs_in_report: bool
    provider_profile: str = "auto"
    signing_key_path: Path | None = None


@dataclass(frozen=True, slots=True)
class LaunchResult:
    """Resultado do caminho rápido validate → deploy → activate → run."""

    deployment: dict[str, Any]
    inference: dict[str, Any] | None
    previous_active_deployment_id: str | None
    rollback: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class PilotOutcome:
    """Resultado final e caminhos das evidências de uma execução."""

    success: bool
    report_json: Path
    report_markdown: Path
    report_signature: Path | None
    report: dict[str, Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat()


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MiraiRuntimeError(f"'{label}' deve ser um objeto JSON")
    return value


def _reject_unknown(
    payload: dict[str, Any],
    allowed: set[str],
    label: str,
) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise MiraiRuntimeError(
            f"'{label}' possui campos desconhecidos: {', '.join(unknown)}"
        )


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MiraiRuntimeError(
                f"projeto de piloto contém chave JSON duplicada: {key}"
            )
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise MiraiRuntimeError(
        f"projeto de piloto contém número não finito: {value}"
    )


def _validate_json_tree(value: Any, label: str) -> None:
    """Limita profundidade, quantidade e números de um valor declarativo."""
    stack: list[tuple[Any, int]] = [(value, 0)]
    visited = 0
    while stack:
        current, depth = stack.pop()
        visited += 1
        if visited > 10_000 or depth > 64:
            raise MiraiRuntimeError(f"'{label}' excede o limite de complexidade")
        if isinstance(current, float) and not math.isfinite(current):
            raise MiraiRuntimeError(f"'{label}' contém número não finito")
        if isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MiraiRuntimeError(f"'{label}' deve ser um número")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise MiraiRuntimeError(f"'{label}' deve ser maior que zero")
    return parsed


def _non_negative_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MiraiRuntimeError(f"'{label}' deve ser um número")
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise MiraiRuntimeError(f"'{label}' não pode ser negativo")
    return parsed


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MiraiRuntimeError(f"'{label}' deve ser um inteiro maior que zero")
    return value


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MiraiRuntimeError(f"'{label}' deve ser um inteiro não negativo")
    return value


def _resolve_relative(base: Path, raw_path: str, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise MiraiRuntimeError(f"'{label}' deve ser um caminho não vazio")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve()


def load_pilot_config(path: Path = DEFAULT_PILOT_CONFIG) -> PilotConfig:
    """Carrega um projeto declarativo com schema estrito e caminhos relativos."""
    source_path = path.expanduser().resolve()
    if not source_path.is_file():
        raise MiraiRuntimeError(
            f"arquivo de piloto não encontrado: {source_path}"
        )
    if source_path.stat().st_size > MAX_CONFIG_SIZE_BYTES:
        raise MiraiRuntimeError("arquivo de piloto excede o limite de 256 KB")

    try:
        raw = json.loads(
            source_path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
    except MiraiRuntimeError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise MiraiRuntimeError(
            f"não foi possível ler o projeto de piloto: {error}"
        ) from error
    payload = _require_object(raw, "projeto")
    _reject_unknown(
        payload,
        {
            "schema_version",
            "name",
            "artifact",
            "device",
            "inputs",
            "layout",
            "benchmark",
            "acceptance",
            "report",
            "runtime",
        },
        "projeto",
    )
    if payload.get("schema_version") != PILOT_SCHEMA_VERSION:
        raise MiraiRuntimeError(
            f"schema_version deve ser {PILOT_SCHEMA_VERSION}"
        )

    name = payload.get("name")
    if not isinstance(name, str) or not PROJECT_NAME_PATTERN.fullmatch(name):
        raise MiraiRuntimeError(
            "'name' deve usar letras, números, ponto, hífen ou sublinhado "
            "(máximo de 64 caracteres)"
        )
    device_name = payload.get("device")
    if not isinstance(device_name, str) or not device_name:
        raise MiraiRuntimeError("'device' deve informar um dispositivo")

    raw_inputs = payload.get("inputs")
    input_specs: list[str] | None
    if raw_inputs is None:
        input_specs = None
    elif (
        not isinstance(raw_inputs, list)
        or not all(isinstance(item, str) and item for item in raw_inputs)
        or len(raw_inputs) > 64
        or any(len(item) > 4096 for item in raw_inputs)
    ):
        raise MiraiRuntimeError(
            "'inputs' deve ser uma lista de até 64 textos não vazios"
        )
    else:
        input_specs = list(raw_inputs)

    layout = payload.get("layout", "auto")
    if layout not in {"auto", "nchw", "nhwc"}:
        raise MiraiRuntimeError("'layout' deve ser auto, nchw ou nhwc")

    runtime_payload = _require_object(payload.get("runtime", {}), "runtime")
    _reject_unknown(runtime_payload, {"provider_profile"}, "runtime")
    raw_provider_profile = runtime_payload.get("provider_profile", "auto")
    if not isinstance(raw_provider_profile, str):
        raise MiraiRuntimeError("'runtime.provider_profile' deve ser uma string")
    provider_profile = normalize_provider_profile(raw_provider_profile)

    benchmark = _require_object(payload.get("benchmark", {}), "benchmark")
    _reject_unknown(benchmark, {"runs", "warmup"}, "benchmark")
    runs = _positive_int(benchmark.get("runs", 20), "benchmark.runs")
    warmup_runs = _non_negative_int(
        benchmark.get("warmup", 3),
        "benchmark.warmup",
    )
    if runs > 10_000 or warmup_runs > 1_000:
        raise MiraiRuntimeError("benchmark excede o limite seguro da CLI")

    acceptance_payload = _require_object(
        payload.get("acceptance", {}),
        "acceptance",
    )
    _reject_unknown(
        acceptance_payload,
        {
            "expected_result",
            "result_tolerance",
            "max_p95_ms",
            "min_ips",
        },
        "acceptance",
    )
    if "expected_result" in acceptance_payload:
        _validate_json_tree(
            acceptance_payload["expected_result"],
            "acceptance.expected_result",
        )
    tolerance = _positive_number(
        acceptance_payload.get("result_tolerance", 1e-6),
        "acceptance.result_tolerance",
    )
    max_p95 = (
        _positive_number(
            acceptance_payload["max_p95_ms"],
            "acceptance.max_p95_ms",
        )
        if "max_p95_ms" in acceptance_payload
        else None
    )
    min_ips = (
        _non_negative_number(
            acceptance_payload["min_ips"],
            "acceptance.min_ips",
        )
        if "min_ips" in acceptance_payload
        else None
    )
    acceptance = PilotAcceptance(
        compare_result="expected_result" in acceptance_payload,
        expected_result=acceptance_payload.get("expected_result"),
        result_tolerance=tolerance,
        max_p95_ms=max_p95,
        min_ips=min_ips,
    )

    report_payload = _require_object(payload.get("report", {}), "report")
    _reject_unknown(
        report_payload,
        {"directory", "include_inputs", "signing_key"},
        "report",
    )
    include_inputs = report_payload.get("include_inputs", False)
    if not isinstance(include_inputs, bool):
        raise MiraiRuntimeError("'report.include_inputs' deve ser booleano")
    report_directory = _resolve_relative(
        source_path.parent,
        report_payload.get("directory", str(DEFAULT_REPORT_DIRECTORY)),
        "report.directory",
    )
    raw_signing_key = report_payload.get("signing_key")
    if raw_signing_key is not None and not isinstance(raw_signing_key, str):
        raise MiraiRuntimeError("'report.signing_key' deve ser texto")
    signing_key_path = (
        _resolve_relative(
            source_path.parent,
            raw_signing_key,
            "report.signing_key",
        )
        if raw_signing_key is not None
        else None
    )
    raw_artifact = payload.get("artifact")
    if not isinstance(raw_artifact, str):
        raise MiraiRuntimeError("'artifact' deve ser texto")
    artifact_path = _resolve_relative(
        source_path.parent,
        raw_artifact,
        "artifact",
    )
    return PilotConfig(
        source_path=source_path,
        name=name,
        artifact_path=artifact_path,
        device_name=device_name,
        input_specs=input_specs,
        layout=layout,
        runs=runs,
        warmup_runs=warmup_runs,
        acceptance=acceptance,
        report_directory=report_directory,
        include_inputs_in_report=include_inputs,
        provider_profile=provider_profile,
        signing_key_path=signing_key_path,
    )


def write_pilot_template(
    path: Path = DEFAULT_PILOT_CONFIG,
    *,
    replace: bool = False,
) -> Path:
    """Cria um projeto de piloto seguro e pronto para personalização."""
    target = path.expanduser().resolve()
    if target.exists() and not replace:
        raise MiraiRuntimeError(
            f"arquivo já existe: {target}; use --replace para substituir"
        )
    payload = {
        "schema_version": PILOT_SCHEMA_VERSION,
        "name": "dummy-local",
        "artifact": "dummy-1.0.0.mirai",
        "device": "local",
        "inputs": ["5.0"],
        "layout": "auto",
        "runtime": {"provider_profile": "auto"},
        "benchmark": {
            "runs": 20,
            "warmup": 3,
        },
        "acceptance": {
            "expected_result": 6.0,
            "result_tolerance": 1e-6,
            "max_p95_ms": 100.0,
            "min_ips": 5.0,
        },
        "report": {
            "directory": ".mirai/reports",
            "include_inputs": False,
            "signing_key": None,
        },
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, target)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise MiraiRuntimeError(
            f"não foi possível criar o projeto de piloto: {error}"
        ) from error
    return target


def _require_compatible_doctor(device: Device) -> dict[str, Any]:
    report = doctor_device(device)
    if not report.get("compatible"):
        agent_version = report.get("health", {}).get(
            "agent_version", "desconhecida"
        )
        raise MiraiRuntimeError(
            "as versões da CLI e do Agent não são compatíveis "
            f"(Agent {agent_version})"
        )
    return report


def _rollback_activation(
    device: Device,
    previous_active_deployment_id: str | None,
    deployed_id: str,
) -> dict[str, Any]:
    current_active = get_deployment_status(device).get("active_deployment_id")
    if current_active != deployed_id:
        return {
            "status": "skipped_active_changed",
            "restored_deployment_id": None,
            "current_active_deployment_id": current_active,
        }
    if previous_active_deployment_id == deployed_id:
        return {
            "status": "not_required",
            "restored_deployment_id": deployed_id,
        }
    if previous_active_deployment_id:
        restored = activate_deployment(
            device,
            previous_active_deployment_id,
        )
        return {
            "status": "restored",
            "restored_deployment_id": restored["deployment_id"],
        }
    deactivated = deactivate_deployment(device)
    return {
        "status": "deactivated",
        "restored_deployment_id": None,
        "deactivated_deployment_id": deactivated.get(
            "previous_deployment_id"
        ),
    }


def launch_artifact(
    artifact_path: Path,
    device_name: str,
    input_specs: list[str] | None = None,
    layout: str = "auto",
    *,
    run_inference: bool = True,
    rollback_on_failure: bool = True,
    provider_profile: str = "auto",
    signature_path: Path | None = None,
) -> LaunchResult:
    """Executa o fluxo rápido com rollback quando a validação final falha."""
    validate_artifact(artifact_path)
    device = get_device(device_name)
    doctor = _require_compatible_doctor(device)
    previous_active = doctor["deployments"].get("active_deployment_id")
    deployment = deploy_model(
        device,
        artifact_path,
        provider_profile,
        signature_path,
    )
    deployed_id = str(deployment["deployment_id"])
    activate_deployment(device, deployed_id)
    inference: dict[str, Any] | None = None
    rollback: dict[str, Any] | None = None
    if run_inference:
        try:
            inference = run_remote_model(
                device,
                input_specs,
                layout,
                artifact_path.name,
            )
        except MiraiRuntimeError:
            if rollback_on_failure:
                rollback = _rollback_activation(
                    device,
                    previous_active,
                    deployed_id,
                )
            raise
    return LaunchResult(
        deployment=deployment,
        inference=inference,
        previous_active_deployment_id=previous_active,
        rollback=rollback,
    )


def rollback_launch(device: Device, launch: LaunchResult) -> dict[str, Any]:
    """Restaura com segurança a ativação anterior de um launch concluído."""
    return _rollback_activation(
        device,
        launch.previous_active_deployment_id,
        str(launch.deployment["deployment_id"]),
    )


def benchmark_remote_model(
    device: Device,
    input_specs: list[str] | None,
    layout: str,
    runs: int,
    warmup_runs: int,
    model_name: str | None = None,
) -> tuple[BenchmarkStats, dict[str, Any]]:
    """Mede no Agent usando a latência de inferência devolvida pelo runtime."""
    last_inference: dict[str, Any] | None = None
    for _ in range(warmup_runs):
        last_inference = run_remote_model(
            device,
            input_specs,
            layout,
            model_name,
        )

    latencies_ms: list[float] = []
    for _ in range(runs):
        last_inference = run_remote_model(
            device,
            input_specs,
            layout,
            model_name,
        )
        latency = last_inference.get("latency_ms")
        if isinstance(latency, bool) or not isinstance(latency, (int, float)):
            raise MiraiRuntimeError("Agent retornou latência de inferência inválida")
        latency_value = float(latency)
        if not math.isfinite(latency_value) or latency_value < 0:
            raise MiraiRuntimeError("Agent retornou latência de inferência inválida")
        latencies_ms.append(latency_value)

    if last_inference is None:
        raise MiraiRuntimeError("benchmark remoto não executou inferências")
    return (
        summarize_latencies(latencies_ms, warmup_runs=warmup_runs),
        last_inference,
    )


def _results_match(actual: Any, expected: Any, tolerance: float) -> bool:
    if (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
    ):
        return math.isclose(
            float(actual),
            float(expected),
            rel_tol=tolerance,
            abs_tol=tolerance,
        )
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _results_match(left, right, tolerance)
            for left, right in zip(actual, expected, strict=False)
        )
    if isinstance(actual, dict) and isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _results_match(actual[key], expected[key], tolerance)
            for key in actual
        )
    return actual == expected


def evaluate_acceptance(
    acceptance: PilotAcceptance,
    inference: dict[str, Any],
    stats: BenchmarkStats,
) -> dict[str, Any]:
    """Compara o resultado observado com todos os critérios declarados."""
    checks: list[dict[str, Any]] = []
    if acceptance.compare_result:
        actual_result = inference.get("result")
        checks.append(
            {
                "criterion": "expected_result",
                "passed": _results_match(
                    actual_result,
                    acceptance.expected_result,
                    acceptance.result_tolerance,
                ),
                "expected": acceptance.expected_result,
                "actual": actual_result,
                "tolerance": acceptance.result_tolerance,
            }
        )
    if acceptance.max_p95_ms is not None:
        checks.append(
            {
                "criterion": "max_p95_ms",
                "passed": stats.p95_ms <= acceptance.max_p95_ms,
                "expected": acceptance.max_p95_ms,
                "actual": stats.p95_ms,
            }
        )
    if acceptance.min_ips is not None:
        checks.append(
            {
                "criterion": "min_ips",
                "passed": stats.inferences_per_second >= acceptance.min_ips,
                "expected": acceptance.min_ips,
                "actual": stats.inferences_per_second,
            }
        )
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def _safe_stats(stats: BenchmarkStats) -> dict[str, Any]:
    payload = asdict(stats)
    return {
        key: (value if not isinstance(value, float) or math.isfinite(value) else None)
        for key, value in payload.items()
    }


def _json_safe(value: Any) -> Any:
    """Converte resultados não finitos em nulo para manter JSON interoperável."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _artifact_evidence(path: Path) -> dict[str, Any]:
    size_kb = validate_artifact(path)
    evidence: dict[str, Any] = {
        "name": path.name,
        "type": "mirai" if path.suffix.lower() == MIRAI_EXTENSION else "onnx",
        "size_bytes": path.stat().st_size,
        "size_kb": size_kb,
        "sha256": calculate_sha256(path),
    }
    if path.suffix.lower() == MIRAI_EXTENSION:
        package = load_mirai_package(path)
        evidence["package"] = {
            "name": package.name,
            "version": package.version,
            "model": package.model_name,
            "format_version": package.manifest["format_version"],
            "model_sha256": package.manifest["model"]["sha256"],
        }
    return evidence


def _safe_device_evidence(
    device: Device,
    doctor: dict[str, Any],
) -> dict[str, Any]:
    health = doctor.get("health", {})
    info = doctor.get("info", {})
    return {
        "name": device.name,
        "url": device.url,
        "agent_id": health.get("agent_id"),
        "agent_version": health.get("agent_version"),
        "tls": doctor.get("tls"),
        "authenticated": doctor.get("authenticated"),
        "system": info.get("system"),
        "release": info.get("release"),
        "machine": info.get("machine"),
        "processor": info.get("processor"),
        "cpu_count": info.get("cpu_count"),
        "memory_bytes": info.get("memory_bytes"),
        "providers": info.get("providers") or [],
    }


def _stage(
    stages: list[dict[str, Any]],
    name: str,
    operation: Callable[[], _T],
    details: Callable[[_T], dict[str, Any] | None] | None = None,
) -> _T:
    started = perf_counter()
    item: dict[str, Any] = {
        "name": name,
        "status": "running",
        "started_at": _iso_utc(),
    }
    stages.append(item)
    try:
        result = operation()
        selected = details(result) if details is not None else None
    except Exception as error:  # noqa: BLE001 - registra qualquer falha da etapa.
        item["status"] = "failed"
        item["duration_ms"] = (perf_counter() - started) * 1000
        item["error"] = str(error)
        raise
    item["status"] = "passed"
    item["duration_ms"] = (perf_counter() - started) * 1000
    if selected:
        item["details"] = selected
    return result


def _markdown_cell(value: Any) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _format_metric(value: Any, suffix: str = "") -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{float(value):.3f}{suffix}"
    return _markdown_cell(value)


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"# Relatório Mirai Pilot — {report['project']['name']}",
        "",
        f"- **Execução:** `{report['run_id']}`",
        f"- **Início:** {report['started_at']}",
        f"- **Fim:** {report['finished_at']}",
        f"- **Status:** **{report['status'].upper()}**",
        f"- **Dispositivo:** {report.get('device', {}).get('name', '—')}",
        f"- **Artefato:** {report.get('artifact', {}).get('name', '—')}",
        "",
        "## Etapas",
        "",
        "| Etapa | Status | Duração |",
        "| --- | --- | ---: |",
    ]
    for stage in report["stages"]:
        lines.append(
            "| "
            f"{_markdown_cell(stage['name'])} | "
            f"{_markdown_cell(stage['status'])} | "
            f"{_format_metric(stage.get('duration_ms'), ' ms')} |"
        )

    metrics = report.get("benchmark")
    if isinstance(metrics, dict):
        lines.extend(
            [
                "",
                "## Benchmark no dispositivo",
                "",
                f"- Média: {_format_metric(metrics.get('average_ms'), ' ms')}",
                f"- Mediana: {_format_metric(metrics.get('median_ms'), ' ms')}",
                f"- P95: {_format_metric(metrics.get('p95_ms'), ' ms')}",
                f"- IPS: {_format_metric(metrics.get('inferences_per_second'))}",
            ]
        )

    acceptance = report.get("acceptance")
    if isinstance(acceptance, dict):
        lines.extend(
            [
                "",
                "## Critérios de sucesso",
                "",
                "| Critério | Esperado | Observado | Resultado |",
                "| --- | --- | --- | --- |",
            ]
        )
        for check in acceptance.get("checks", []):
            result = "aprovado" if check.get("passed") else "reprovado"
            lines.append(
                "| "
                f"{_markdown_cell(check.get('criterion'))} | "
                f"{_markdown_cell(check.get('expected'))} | "
                f"{_markdown_cell(check.get('actual'))} | {result} |"
            )

    rollback = report.get("rollback")
    if isinstance(rollback, dict):
        lines.extend(
            [
                "",
                "## Rollback",
                "",
                f"- Status: {rollback.get('status', 'desconhecido')}",
                ("- Deployment restaurado: "
                f"{rollback.get('restored_deployment_id') or 'nenhum'}"),
            ]
        )

    if report.get("error"):
        lines.extend(["", "## Erro", "", f"`{_markdown_cell(report['error'])}`"])
    lines.extend(
        [
            "",
            "## Conclusão",
            "",
            (
                "O piloto cumpriu todos os critérios declarados."
                if report["status"] == "passed"
                else "O piloto não foi aprovado; consulte as etapas e o rollback."
            ),
            "",
            "Gerado automaticamente pelo MiraiOS. The Future Runs Local.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_report(
    report: dict[str, Any],
    directory: Path,
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{report['project']['name']}-{report['run_id']}"
    json_path = directory / f"{stem}.json"
    markdown_path = directory / f"{stem}.md"
    files = (
        (
            json_path,
            json.dumps(
                report,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n",
        ),
        (markdown_path, _render_markdown(report)),
    )
    for target, content in files:
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, target)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise MiraiRuntimeError(
                f"não foi possível escrever o relatório: {error}"
            ) from error
    return json_path, markdown_path


def run_pilot(config: PilotConfig) -> PilotOutcome:
    """Executa o piloto completo e sempre produz evidências após iniciar."""
    started = _utc_now()
    run_id = f"{started.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    stages: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": _iso_utc(started),
        "finished_at": None,
        "status": "running",
        "project": {
            "name": config.name,
            "config": config.source_path.name,
            "device": config.device_name,
            "layout": config.layout,
            "input_count": len(config.input_specs or []),
            "inputs_recorded": config.include_inputs_in_report,
            "inputs": (
                config.input_specs if config.include_inputs_in_report else None
            ),
        },
        "stages": stages,
        "artifact": None,
        "device": None,
        "deployment": None,
        "inference": None,
        "benchmark": None,
        "acceptance": None,
        "rollback": None,
        "error": None,
    }
    device: Device | None = None
    previous_active: str | None = None
    deployed_id: str | None = None

    try:
        report["artifact"] = _stage(
            stages,
            "validate_artifact",
            lambda: _artifact_evidence(config.artifact_path),
            lambda value: {
                "sha256": value["sha256"],
                "type": value["type"],
            },
        )
        device = _stage(
            stages,
            "resolve_device",
            lambda: get_device(config.device_name),
            lambda value: {"name": value.name, "url": value.url},
        )
        doctor = _stage(
            stages,
            "doctor",
            lambda: _require_compatible_doctor(device),
            lambda value: {
                "compatible": value["compatible"],
                "providers": value["info"].get("providers") or [],
            },
        )
        report["device"] = _safe_device_evidence(device, doctor)
        previous_active = doctor["deployments"].get("active_deployment_id")

        deployment = _stage(
            stages,
            "deploy",
            lambda: deploy_model(
                device,
                config.artifact_path,
                config.provider_profile,
            ),
            lambda value: {
                "deployment_id": value["deployment_id"],
                "status": value["status"],
            },
        )
        deployed_id = str(deployment["deployment_id"])
        report["deployment"] = {
            "previous_active_deployment_id": previous_active,
            "deployment_id": deployed_id,
            "artifact_name": deployment.get("artifact_name"),
            "model": deployment.get("model"),
            "sha256": deployment.get("sha256"),
            "providers": deployment.get("providers") or [],
            "provider_profile": deployment.get("provider_profile"),
            "package": deployment.get("package"),
        }
        _stage(
            stages,
            "activate",
            lambda: activate_deployment(device, deployed_id),
            lambda value: {
                "deployment_id": value["deployment_id"],
                "status": value["status"],
            },
        )
        inference = _stage(
            stages,
            "health_inference",
            lambda: run_remote_model(
                device,
                config.input_specs,
                config.layout,
                config.artifact_path.name,
            ),
            lambda value: {
                "deployment_id": value["deployment_id"],
                "latency_ms": value["latency_ms"],
            },
        )
        report["inference"] = inference
        stats, last_inference = _stage(
            stages,
            "benchmark",
            lambda: benchmark_remote_model(
                device,
                config.input_specs,
                config.layout,
                config.runs,
                config.warmup_runs,
                config.artifact_path.name,
            ),
            lambda value: {
                "runs": value[0].runs,
                "p95_ms": value[0].p95_ms,
                "ips": value[0].inferences_per_second,
            },
        )
        report["benchmark"] = _safe_stats(stats)
        acceptance = _stage(
            stages,
            "acceptance",
            lambda: evaluate_acceptance(
                config.acceptance,
                last_inference,
                stats,
            ),
            lambda value: {
                "passed": value["passed"],
                "checks": len(value["checks"]),
            },
        )
        report["acceptance"] = acceptance
        if not acceptance["passed"]:
            raise MiraiRuntimeError(
                "um ou mais critérios de sucesso não foram cumpridos"
            )
        final_status = _stage(
            stages,
            "final_status",
            lambda: get_deployment_status(device),
            lambda value: {
                "active_deployment_id": value.get("active_deployment_id")
            },
        )
        if final_status.get("active_deployment_id") != deployed_id:
            raise MiraiRuntimeError(
                "o deployment aprovado não permaneceu ativo no dispositivo"
            )
        report["status"] = "passed"
    except Exception as error:  # noqa: BLE001 - transação precisa acionar rollback.
        report["status"] = "failed"
        report["error"] = str(error)
        if device is not None and deployed_id is not None:
            try:
                report["rollback"] = _stage(
                    stages,
                    "rollback",
                    lambda: _rollback_activation(
                        device,
                        previous_active,
                        deployed_id,
                    ),
                    lambda value: value,
                )
            except Exception as rollback_error:  # noqa: BLE001 - preserva a falha original.
                report["rollback"] = {
                    "status": "failed",
                    "error": str(rollback_error),
                }
    report["finished_at"] = _iso_utc()
    safe_report = _json_safe(report)
    json_path, markdown_path = _write_report(
        safe_report,
        config.report_directory,
    )
    signature_path: Path | None = None
    if config.signing_key_path is not None:
        signed = sign_artifact(
            json_path,
            config.signing_key_path,
            replace=True,
        )
        signature_path = Path(signed["signature"])
    return PilotOutcome(
        success=safe_report["status"] == "passed",
        report_json=json_path,
        report_markdown=markdown_path,
        report_signature=signature_path,
        report=safe_report,
    )
