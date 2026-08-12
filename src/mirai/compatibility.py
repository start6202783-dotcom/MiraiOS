"""Matriz local de compatibilidade construída a partir de resultados da Arena."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .arena import load_arena_result
from .errors import MiraiRuntimeError
from .json_codec import strict_json_dumps, strict_json_loads
from .signing import verify_artifact
from .storage import atomic_write_text, stable_file_digest

MATRIX_FORMAT = "mirai.compatibility-matrix"
MATRIX_VERSION = 1
MAX_MATRIX_BYTES = 16 * 1024 * 1024
MAX_MATRIX_ENTRIES = 10_000


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_entry(entry: object) -> dict[str, Any]:
    required = {
        "result_id",
        "report_sha256",
        "created_at",
        "suite",
        "subject",
        "hardware",
        "runtime",
        "metrics",
        "verification",
    }
    if not isinstance(entry, dict) or set(entry) != required:
        raise MiraiRuntimeError("entrada da Compatibility Matrix inválida")
    if (
        not isinstance(entry.get("result_id"), str)
        or not entry["result_id"].startswith("result-")
        or not _is_sha256(entry.get("report_sha256"))
        or not isinstance(entry.get("created_at"), str)
    ):
        raise MiraiRuntimeError("identidade de resultado inválida na matriz")
    suite = entry.get("suite")
    hardware = entry.get("hardware")
    subject = entry.get("subject")
    runtime = entry.get("runtime")
    metrics = entry.get("metrics")
    verification = entry.get("verification")
    if not isinstance(suite, dict) or set(suite) != {
        "name",
        "version",
        "scenario",
        "workload_id",
        "category",
    }:
        raise MiraiRuntimeError("suite inválida na matriz")
    if not isinstance(suite.get("workload_id"), str):
        raise MiraiRuntimeError("workload inválido na matriz")
    if not isinstance(hardware, dict) or set(hardware) != {
        "hardware_id",
        "environment_id",
        "architecture_class",
        "board_model",
        "processor",
        "cpu_count",
        "memory_bytes",
    }:
        raise MiraiRuntimeError("hardware inválido na matriz")
    if not all(
        isinstance(hardware.get(field), str)
        for field in (
            "hardware_id",
            "environment_id",
            "architecture_class",
            "board_model",
            "processor",
        )
    ):
        raise MiraiRuntimeError("identidade de hardware inválida na matriz")
    if not isinstance(subject, dict) or not _is_sha256(subject.get("sha256")):
        raise MiraiRuntimeError("subject inválido na matriz")
    if not isinstance(runtime, dict) or not isinstance(
        runtime.get("selected_providers"), list
    ):
        raise MiraiRuntimeError("runtime inválido na matriz")
    if not isinstance(metrics, dict) or not _is_sha256(metrics.get("samples_sha256")):
        raise MiraiRuntimeError("métricas inválidas na matriz")
    if not isinstance(verification, dict) or set(verification) != {"level", "key_id"}:
        raise MiraiRuntimeError("verificação inválida na matriz")
    level = verification.get("level")
    key_id = verification.get("key_id")
    if level == "self-measured" and key_id is not None:
        raise MiraiRuntimeError("resultado self-measured não pode declarar chave")
    if level == "signed-verified" and not _is_sha256(key_id):
        raise MiraiRuntimeError("chave de verificação inválida na matriz")
    if level not in {"self-measured", "signed-verified"}:
        raise MiraiRuntimeError("nível de verificação inválido na matriz")
    return entry


def _entry_from_result(
    result: dict[str, Any],
    report_digest: str,
    signature: dict[str, Any] | None,
) -> dict[str, Any]:
    hardware = result["hardware"]
    suite = result["suite"]
    return {
        "result_id": result["result_id"],
        "report_sha256": report_digest,
        "created_at": result["created_at"],
        "suite": {
            "name": suite["name"],
            "version": suite["version"],
            "scenario": suite["scenario"],
            "workload_id": suite["workload_id"],
            "category": suite["category"],
        },
        "subject": dict(result["subject"]),
        "hardware": {
            "hardware_id": hardware["hardware_id"],
            "environment_id": hardware["environment_id"],
            "architecture_class": hardware["architecture_class"],
            "board_model": hardware["board_model"],
            "processor": hardware["processor"],
            "cpu_count": hardware["cpu_count"],
            "memory_bytes": hardware["memory_bytes"],
        },
        "runtime": dict(result["runtime"]),
        "metrics": dict(result["metrics"]),
        "verification": (
            {
                "level": "signed-verified",
                "key_id": signature["key_id"],
            }
            if signature is not None
            else {"level": "self-measured", "key_id": None}
        ),
    }


def _readiness(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        grouped[
            (entry["hardware"]["hardware_id"], entry["suite"]["workload_id"])
        ].append(entry)
    readiness: list[dict[str, Any]] = []
    for (hardware_id, workload_id), group in sorted(grouped.items()):
        signed = [
            entry
            for entry in group
            if entry["verification"]["level"] == "signed-verified"
        ]
        key_ids = {
            entry["verification"]["key_id"]
            for entry in signed
            if entry["verification"]["key_id"]
        }
        environment_ids = {entry["hardware"]["environment_id"] for entry in signed}
        candidate = len(signed) >= 3 and len(key_ids) >= 2 and len(environment_ids) >= 2
        if candidate:
            status = "candidate-for-independent-review"
        elif signed:
            status = "signed-evidence"
        else:
            status = "observed"
        readiness.append(
            {
                "hardware_id": hardware_id,
                "workload_id": workload_id,
                "status": status,
                "result_count": len(group),
                "signed_result_count": len(signed),
                "distinct_signing_keys": len(key_ids),
                "distinct_environments": len(environment_ids),
                "certification_status": "not-certified",
            }
        )
    return readiness


def _summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    levels = Counter(entry["verification"]["level"] for entry in entries)
    return {
        "result_count": len(entries),
        "hardware_count": len(
            {entry["hardware"]["hardware_id"] for entry in entries}
        ),
        "environment_count": len(
            {entry["hardware"]["environment_id"] for entry in entries}
        ),
        "workload_count": len({entry["suite"]["workload_id"] for entry in entries}),
        "verification_levels": dict(sorted(levels.items())),
        "readiness": _readiness(entries),
        "certification": {
            "self_certification_supported": False,
            "statement": (
                "a matriz organiza evidências; certificação exige um programa "
                "independente futuro"
            ),
        },
    }


def _new_matrix() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    return {
        "format": MATRIX_FORMAT,
        "schema_version": MATRIX_VERSION,
        "entries": entries,
        "summary": _summarize(entries),
    }


def load_compatibility_matrix(path: Path, *, allow_missing: bool = False) -> dict[str, Any]:
    target = path.expanduser().resolve()
    if allow_missing and not target.exists():
        return _new_matrix()
    try:
        size = target.stat().st_size
        content = target.read_bytes()
    except OSError as error:
        raise MiraiRuntimeError(
            f"não foi possível ler a Compatibility Matrix: {error}"
        ) from error
    if not 0 < size <= MAX_MATRIX_BYTES:
        raise MiraiRuntimeError("Compatibility Matrix vazia ou acima de 16 MB")
    matrix = strict_json_loads(content, label="Compatibility Matrix")
    if not isinstance(matrix, dict) or set(matrix) != {
        "format",
        "schema_version",
        "entries",
        "summary",
    }:
        raise MiraiRuntimeError("Compatibility Matrix possui campos incompatíveis")
    if matrix["format"] != MATRIX_FORMAT or matrix["schema_version"] != MATRIX_VERSION:
        raise MiraiRuntimeError("versão da Compatibility Matrix não suportada")
    entries = matrix.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_MATRIX_ENTRIES:
        raise MiraiRuntimeError("entradas da Compatibility Matrix inválidas")
    result_ids: set[str] = set()
    for raw_entry in entries:
        entry = _validate_entry(raw_entry)
        result_id = entry.get("result_id")
        if not isinstance(result_id, str) or not result_id or result_id in result_ids:
            raise MiraiRuntimeError("result_id duplicado ou inválido na matriz")
        result_ids.add(result_id)
    ordered = sorted(entries, key=lambda item: item["result_id"])
    if entries != ordered:
        raise MiraiRuntimeError("entradas da Compatibility Matrix não estão ordenadas")
    if matrix.get("summary") != _summarize(entries):
        raise MiraiRuntimeError("resumo da Compatibility Matrix não corresponde às entradas")
    return matrix


def add_compatibility_result(
    matrix_path: Path,
    result_path: Path,
    *,
    signature_path: Path | None = None,
    public_key_path: Path | None = None,
) -> dict[str, Any]:
    """Adiciona um resultado local, verificando a assinatura quando informada."""
    if (signature_path is None) != (public_key_path is None):
        raise MiraiRuntimeError(
            "--signature e --key devem ser informados juntos"
        )
    result_target = result_path.expanduser().resolve()
    result = load_arena_result(result_target)
    report_digest, _ = stable_file_digest(result_target)
    signature: dict[str, Any] | None = None
    if signature_path is not None and public_key_path is not None:
        signature = verify_artifact(
            result_target,
            signature_path,
            public_key_path,
        )
    target = matrix_path.expanduser().resolve()
    matrix = load_compatibility_matrix(target, allow_missing=True)
    new_entry = _entry_from_result(result, report_digest, signature)
    replacement_id: str | None = None
    for existing in matrix["entries"]:
        if existing["result_id"] != new_entry["result_id"]:
            continue
        if existing == new_entry:
            return matrix
        same_report = existing.get("report_sha256") == new_entry["report_sha256"]
        existing_level = existing.get("verification", {}).get("level")
        new_level = new_entry["verification"]["level"]
        if same_report and existing_level == "signed-verified" and new_level == "self-measured":
            return matrix
        if same_report and existing_level == "self-measured" and new_level == "signed-verified":
            replacement_id = new_entry["result_id"]
            break
        raise MiraiRuntimeError(
            "o result_id já existe com conteúdo ou verificação diferente"
        )
    if replacement_id is None and len(matrix["entries"]) >= MAX_MATRIX_ENTRIES:
        raise MiraiRuntimeError("Compatibility Matrix atingiu 10.000 resultados")
    retained = [
        entry
        for entry in matrix["entries"]
        if entry["result_id"] != replacement_id
    ]
    entries = sorted(
        [*retained, new_entry],
        key=lambda item: item["result_id"],
    )
    updated = {
        "format": MATRIX_FORMAT,
        "schema_version": MATRIX_VERSION,
        "entries": entries,
        "summary": _summarize(entries),
    }
    try:
        atomic_write_text(
            target,
            strict_json_dumps(updated, pretty=True, sort_keys=True) + "\n",
            mode=0o600,
        )
    except OSError as error:
        raise MiraiRuntimeError(
            f"não foi possível gravar a Compatibility Matrix: {error}"
        ) from error
    return updated
