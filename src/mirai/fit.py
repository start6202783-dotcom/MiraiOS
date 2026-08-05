"""Mirai Fit v1: quantização controlada, benchmark e gate de qualidade."""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .benchmark import BenchmarkStats, benchmark_session
from .errors import MiraiRuntimeError
from .inspect import validate_model
from .json_codec import strict_json_dumps
from .package import create_mirai_package
from .providers import hardware_profile
from .runtime import create_session, load_runtime_dependencies, prepare_inference
from .signing import sign_artifact
from .storage import atomic_write_text

FIT_REPORT_VERSION = 1
SIGNATURE_SUFFIX = ".dsse.json"


@dataclass(frozen=True, slots=True)
class FitOutcome:
    """Resultado verificável da geração de uma variante quantizada."""

    accepted: bool
    package_path: Path | None
    signature_path: Path | None
    report_path: Path
    report: dict[str, Any]


def _stats(stats: BenchmarkStats) -> dict[str, Any]:
    payload = asdict(stats)
    return {
        key: None if isinstance(value, float) and not math.isfinite(value) else value
        for key, value in payload.items()
    }


def _compare_outputs(baseline: list[Any], candidate: list[Any], np: Any) -> dict[str, Any]:
    if len(baseline) != len(candidate):
        raise MiraiRuntimeError("a variante alterou a quantidade de saídas")
    max_absolute_error = 0.0
    max_relative_error = 0.0
    compared_values = 0
    for index, (expected, actual) in enumerate(zip(baseline, candidate, strict=True)):
        expected_array = np.asarray(expected)
        actual_array = np.asarray(actual)
        if expected_array.shape != actual_array.shape:
            raise MiraiRuntimeError(f"a variante alterou o shape da saída {index}")
        if not np.issubdtype(expected_array.dtype, np.number) or not np.issubdtype(
            actual_array.dtype, np.number
        ):
            raise MiraiRuntimeError("Mirai Fit v1 compara somente saídas numéricas")
        expected_float = expected_array.astype(np.float64, copy=False)
        actual_float = actual_array.astype(np.float64, copy=False)
        if not np.all(np.isfinite(expected_float)) or not np.all(np.isfinite(actual_float)):
            raise MiraiRuntimeError("saída não finita impede avaliar a variante")
        absolute = np.abs(expected_float - actual_float)
        denominator = np.maximum(np.abs(expected_float), 1e-12)
        relative = absolute / denominator
        if absolute.size:
            max_absolute_error = max(max_absolute_error, float(np.max(absolute)))
            max_relative_error = max(max_relative_error, float(np.max(relative)))
            compared_values += int(absolute.size)
    return {
        "compared_values": compared_values,
        "max_absolute_error": max_absolute_error,
        "max_relative_error": max_relative_error,
    }


def _quantize_dynamic(source: Path, target: Path, *, per_channel: bool) -> None:
    try:
        from onnxruntime.quantization import (  # type: ignore[import-untyped]
            QuantType,
            quantize_dynamic,
        )
    except (ImportError, ModuleNotFoundError) as error:
        raise MiraiRuntimeError(
            "esta instalação do ONNX Runtime não oferece quantização"
        ) from error
    try:
        quantize_dynamic(
            str(source),
            str(target),
            weight_type=QuantType.QInt8,
            per_channel=per_channel,
        )
    except Exception as error:
        raise MiraiRuntimeError(f"falha ao quantizar o modelo: {error}") from error


def _publish_outputs(
    staged: list[tuple[Path, Path]],
    *,
    replace: bool,
    remove_on_success: tuple[Path, ...] = (),
) -> None:
    """Publica vários artefatos com restauração dos anteriores em caso de falha."""
    if not staged:
        return
    targets = [target for _, target in staged]
    if len(targets) != len(set(targets)):
        raise MiraiRuntimeError("destinos duplicados na publicação do Mirai Fit")
    managed_targets = [*targets, *[path for path in remove_on_success if path not in targets]]
    if not replace:
        existing = next((path for path in managed_targets if path.exists()), None)
        if existing is not None:
            raise MiraiRuntimeError(f"arquivo de saída já existe: {existing}")

    backup_directory = staged[0][0].parent / ".fit-backups"
    backups: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        if replace:
            backup_directory.mkdir(mode=0o700)
            for index, target in enumerate(managed_targets):
                if target.exists():
                    backup = backup_directory / f"{index}-{target.name}"
                    os.replace(target, backup)
                    backups[target] = backup
        for source, target in staged:
            os.replace(source, target)
            committed.append(target)
    except OSError as error:
        for target in reversed(committed):
            target.unlink(missing_ok=True)
        for target, backup in backups.items():
            try:
                os.replace(backup, target)
            except OSError:
                pass
        raise MiraiRuntimeError(f"não foi possível publicar a variante: {error}") from error


def fit_model(
    model_path: Path,
    output_path: Path,
    *,
    name: str,
    package_version: str,
    input_specs: list[str] | None = None,
    layout: str = "auto",
    runs: int = 50,
    warmup_runs: int = 3,
    max_absolute_error: float = 0.05,
    min_speedup: float = 1.0,
    per_channel: bool = False,
    signing_key_path: Path | None = None,
    replace: bool = False,
) -> FitOutcome:
    """Gera INT8, compara com FP32 e publica apenas uma variante aprovada."""
    source = model_path.expanduser().resolve()
    output = output_path.expanduser().resolve()
    if source.suffix.lower() != ".onnx":
        raise MiraiRuntimeError("Mirai Fit v1 aceita um modelo ONNX como origem")
    if output.suffix.lower() != ".mirai":
        raise MiraiRuntimeError("a saída do Mirai Fit deve usar a extensão .mirai")
    if output.exists() and not replace:
        raise MiraiRuntimeError(f"arquivo de saída já existe: {output}")
    if runs <= 0 or warmup_runs < 0:
        raise MiraiRuntimeError("runs deve ser positivo e warmup não negativo")
    if (
        not math.isfinite(max_absolute_error)
        or max_absolute_error < 0
        or not math.isfinite(min_speedup)
        or min_speedup < 0
    ):
        raise MiraiRuntimeError("limites do Mirai Fit devem ser finitos e não negativos")

    validate_model(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path = output.with_name(output.name + ".fit.json")
    if report_path.exists() and not replace:
        raise MiraiRuntimeError(f"relatório já existe: {report_path}")

    with tempfile.TemporaryDirectory(
        prefix=".mirai-fit-",
        dir=output.parent,
    ) as temporary_directory:
        quantized = Path(temporary_directory) / "model-int8.onnx"
        _quantize_dynamic(source, quantized, per_channel=per_channel)
        validate_model(quantized)

        ort, np = load_runtime_dependencies()
        baseline_session, input_feed = prepare_inference(
            source,
            input_specs,
            layout,
            provider_profile="cpu",
        )
        candidate_session = create_session(quantized, ort, "cpu")
        baseline_outputs = baseline_session.run(None, input_feed)
        candidate_outputs = candidate_session.run(None, input_feed)
        quality = _compare_outputs(baseline_outputs, candidate_outputs, np)
        baseline_stats = benchmark_session(
            baseline_session,
            input_feed,
            runs,
            warmup_runs,
        )
        candidate_stats = benchmark_session(
            candidate_session,
            input_feed,
            runs,
            warmup_runs,
        )
        del baseline_session, candidate_session

        speedup = (
            baseline_stats.p95_ms / candidate_stats.p95_ms
            if candidate_stats.p95_ms > 0
            else (1.0 if baseline_stats.p95_ms == 0 else 1e12)
        )
        quality_passed = quality["max_absolute_error"] <= max_absolute_error
        performance_passed = speedup >= min_speedup
        accepted = quality_passed and performance_passed
        profile = hardware_profile(providers=list(ort.get_available_providers()))
        report: dict[str, Any] = {
            "schema_version": FIT_REPORT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "accepted" if accepted else "rejected",
            "source": {
                "path": str(source),
                "size_bytes": source.stat().st_size,
                "precision": "fp32-or-source",
            },
            "candidate": {
                "method": "onnxruntime-dynamic-int8",
                "per_channel": per_channel,
                "size_bytes": quantized.stat().st_size,
                "package": str(output) if accepted else None,
            },
            "hardware": profile,
            "benchmark": {
                "baseline": _stats(baseline_stats),
                "candidate": _stats(candidate_stats),
                "p95_speedup": speedup,
            },
            "quality": quality,
            "policy": {
                "max_absolute_error": max_absolute_error,
                "min_speedup": min_speedup,
            },
            "checks": {
                "quality_passed": quality_passed,
                "performance_passed": performance_passed,
            },
            "limitations": [
                "benchmark executado no host do control plane",
                "um conjunto representativo de entradas continua necessário",
                "aprovação não certifica precisão para uso regulado",
            ],
        }
        package_path: Path | None = None
        signature_path: Path | None = None
        staged: list[tuple[Path, Path]] = []
        temporary_root = Path(temporary_directory)
        if accepted:
            staged_package = temporary_root / output.name
            package = create_mirai_package(
                quantized,
                staged_package,
                name=name,
                package_version=package_version,
                description="Variante INT8 aprovada pelo Mirai Fit v1",
            )
            package_path = output
            staged.append((package.path, output))
            if signing_key_path is not None:
                staged_signature = temporary_root / (output.name + SIGNATURE_SUFFIX)
                signed = sign_artifact(
                    package.path,
                    signing_key_path,
                    signature_path=staged_signature,
                )
                signature_path = output.with_name(output.name + SIGNATURE_SUFFIX)
                staged.append((Path(signed["signature"]), signature_path))
                report["candidate"]["signature"] = str(signature_path)

        staged_report = temporary_root / report_path.name
        try:
            atomic_write_text(
                staged_report,
                strict_json_dumps(report, pretty=True) + "\n",
                mode=0o600,
            )
        except OSError as error:
            raise MiraiRuntimeError(
                f"não foi possível gravar o relatório do Mirai Fit: {error}"
            ) from error
        staged.append((staged_report, report_path))
        stale_signature = output.with_name(output.name + SIGNATURE_SUFFIX)
        _publish_outputs(
            staged,
            replace=replace,
            remove_on_success=(stale_signature,)
            if accepted and signing_key_path is None
            else (),
        )

    return FitOutcome(
        accepted=accepted,
        package_path=package_path,
        signature_path=signature_path,
        report_path=report_path,
        report=report,
    )
