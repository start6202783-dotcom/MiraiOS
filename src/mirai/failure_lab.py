"""Laboratório local e controlado de falhas para artefatos MiraiOS."""

from __future__ import annotations

import hashlib
import math
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .benchmark import benchmark_model
from .errors import MiraiRuntimeError
from .hardware import collect_hardware_identity
from .inspect import validate_artifact
from .json_codec import canonical_json_bytes, strict_json_dumps, strict_json_loads
from .runtime import load_runtime_dependencies, run_model
from .signing import sign_artifact
from .storage import atomic_write_bytes, atomic_write_text, stable_file_digest

FAILURE_LAB_FORMAT = "mirai.failure-lab"
FAILURE_LAB_VERSION = 1
MAX_FAILURE_LAB_REPORT_BYTES = 2 * 1024 * 1024


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _check(
    checks: list[dict[str, Any]],
    name: str,
    operation: Callable[[], tuple[bool, dict[str, Any]]],
) -> None:
    try:
        passed, details = operation()
        checks.append({"name": name, "passed": bool(passed), "details": details})
    except Exception as error:  # noqa: BLE001 - o relatório deve registrar a falha.
        checks.append(
            {
                "name": name,
                "passed": False,
                "details": {"error": str(error)},
            }
        )


def _must_be_rejected(path: Path) -> tuple[bool, dict[str, Any]]:
    try:
        validate_artifact(path)
    except MiraiRuntimeError as error:
        return True, {"rejected": True, "reason": str(error)[:512]}
    return False, {"rejected": False, "reason": "artefato inválido foi aceito"}


def run_failure_lab(
    artifact_path: Path,
    output_path: Path,
    *,
    input_specs: list[str] | None = None,
    layout: str = "auto",
    provider_profile: str = "auto",
    benchmark_runs: int = 3,
    signing_key_path: Path | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Injeta corrupção e truncamento somente em cópias temporárias."""
    if benchmark_runs <= 0:
        raise MiraiRuntimeError("benchmark_runs deve ser positivo")
    artifact = artifact_path.expanduser().resolve()
    output = output_path.expanduser().resolve()
    if output.suffix.lower() != ".json":
        raise MiraiRuntimeError("o relatório do Failure Lab deve usar .json")
    if output.exists() and not replace:
        raise MiraiRuntimeError(f"arquivo de saída já existe: {output}")
    validate_artifact(artifact)
    original_digest, original_size = stable_file_digest(artifact)
    if original_size < 2:
        raise MiraiRuntimeError("artefato pequeno demais para testes de falha")
    try:
        original_content = artifact.read_bytes()
    except OSError as error:
        raise MiraiRuntimeError(f"não foi possível ler o artefato: {error}") from error
    if hashlib.sha256(original_content).hexdigest() != original_digest:
        raise MiraiRuntimeError("artefato foi alterado durante a preparação do laboratório")

    ort, _ = load_runtime_dependencies()
    providers = list(ort.get_available_providers())
    checks: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="mirai-failure-lab-") as temporary:
        directory = Path(temporary)
        corrupt_path = directory / artifact.name
        corrupted = bytearray(original_content)
        corrupted[0] ^= 0xFF
        atomic_write_bytes(corrupt_path, bytes(corrupted), mode=0o600)
        _check(
            checks,
            "corruption-detected",
            lambda: _must_be_rejected(corrupt_path),
        )

        truncated_path = directory / f"truncated-{artifact.name}"
        atomic_write_bytes(
            truncated_path,
            original_content[: max(1, original_size // 2)],
            mode=0o600,
        )
        _check(
            checks,
            "truncation-detected",
            lambda: _must_be_rejected(truncated_path),
        )

    first_result: object | None = None

    def repeatability() -> tuple[bool, dict[str, Any]]:
        nonlocal first_result
        first_result, _ = run_model(
            artifact,
            input_specs,
            layout,
            provider_profile=provider_profile,
        )
        second_result, _ = run_model(
            artifact,
            input_specs,
            layout,
            provider_profile=provider_profile,
        )
        first_safe = _json_safe(first_result)
        second_safe = _json_safe(second_result)
        return first_safe == second_safe, {
            "normalized_result_sha256": hashlib.sha256(
                canonical_json_bytes(first_safe)
            ).hexdigest(),
            "second_result_sha256": hashlib.sha256(
                canonical_json_bytes(second_safe)
            ).hexdigest(),
        }

    _check(checks, "repeatable-output", repeatability)

    def benchmark_smoke() -> tuple[bool, dict[str, Any]]:
        stats = benchmark_model(
            artifact,
            benchmark_runs,
            1,
            input_specs,
            layout,
            provider_profile,
        )
        details = _json_safe(asdict(stats))
        valid = (
            stats.runs == benchmark_runs
            and stats.p95_ms >= 0
            and stats.inferences_per_second > 0
        )
        return valid, details

    _check(checks, "benchmark-smoke", benchmark_smoke)

    def original_unchanged() -> tuple[bool, dict[str, Any]]:
        current_digest, current_size = stable_file_digest(artifact)
        matches = current_digest == original_digest and current_size == original_size
        return matches, {
            "sha256": current_digest,
            "size_bytes": current_size,
        }

    _check(checks, "original-unchanged", original_unchanged)
    report: dict[str, Any] = {
        "format": FAILURE_LAB_FORMAT,
        "schema_version": FAILURE_LAB_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "subject": {
            "name": artifact.name,
            "sha256": original_digest,
            "size_bytes": original_size,
        },
        "environment": collect_hardware_identity(providers=providers),
        "configuration": {
            "layout": layout,
            "provider_profile": provider_profile,
            "benchmark_runs": benchmark_runs,
            "input_count": len(input_specs or []),
            "inputs_included": False,
        },
        "status": "passed" if all(item["passed"] for item in checks) else "failed",
        "checks": checks,
        "limitations": [
            "não é sandbox, fuzzer ou prova de segurança",
            "falhas são injetadas somente em cópias temporárias locais",
            "aprovação não equivale a certificação de hardware ou produção",
        ],
        "created_by": {"tool": "miraios", "version": __version__},
    }
    report["lab_id"] = "lab-" + hashlib.sha256(
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
            f"não foi possível gravar o relatório do Failure Lab: {error}"
        ) from error
    if signing_key_path is not None:
        sign_artifact(output, signing_key_path, replace=replace)
    return report


def load_failure_lab_report(path: Path) -> dict[str, Any]:
    target = path.expanduser().resolve()
    try:
        size = target.stat().st_size
        content = target.read_bytes()
    except OSError as error:
        raise MiraiRuntimeError(
            f"não foi possível ler o relatório do Failure Lab: {error}"
        ) from error
    if not 0 < size <= MAX_FAILURE_LAB_REPORT_BYTES:
        raise MiraiRuntimeError("relatório do Failure Lab vazio ou acima de 2 MB")
    report = strict_json_loads(content, label="relatório do Failure Lab")
    required = {
        "format",
        "schema_version",
        "created_at",
        "subject",
        "environment",
        "configuration",
        "status",
        "checks",
        "limitations",
        "created_by",
        "lab_id",
    }
    if not isinstance(report, dict) or set(report) != required:
        raise MiraiRuntimeError("relatório do Failure Lab deve ser um objeto JSON")
    if (
        report.get("format") != FAILURE_LAB_FORMAT
        or report.get("schema_version") != FAILURE_LAB_VERSION
    ):
        raise MiraiRuntimeError("versão do relatório do Failure Lab não suportada")
    subject = report.get("subject")
    if not isinstance(subject, dict) or set(subject) != {"name", "sha256", "size_bytes"}:
        raise MiraiRuntimeError("subject do Failure Lab inválido")
    digest = subject.get("sha256")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise MiraiRuntimeError("SHA-256 do Failure Lab inválido")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        raise MiraiRuntimeError("checks do Failure Lab inválidos")
    for check in checks:
        if not isinstance(check, dict) or set(check) != {"name", "passed", "details"}:
            raise MiraiRuntimeError("check do Failure Lab inválido")
        if not isinstance(check.get("name"), str) or not isinstance(
            check.get("passed"), bool
        ):
            raise MiraiRuntimeError("resultado de check do Failure Lab inválido")
    expected_status = "passed" if all(check["passed"] for check in checks) else "failed"
    if report.get("status") != expected_status:
        raise MiraiRuntimeError("status do Failure Lab não corresponde aos checks")
    claimed_id = report.get("lab_id")
    without_id = dict(report)
    without_id.pop("lab_id", None)
    expected_id = "lab-" + hashlib.sha256(
        canonical_json_bytes(without_id)
    ).hexdigest()[:24]
    if claimed_id != expected_id:
        raise MiraiRuntimeError("identificador do Failure Lab não confere")
    return report
