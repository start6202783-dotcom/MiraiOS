"""Consulta e retenção segura das evidências do Mirai Pilot."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .errors import MiraiRuntimeError

MAX_REPORT_SIZE_BYTES = 2 * 1024 * 1024
RUN_ID_PATTERN = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
VALID_STATUSES = {"passed", "failed", "running"}


def _load_report(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_REPORT_SIZE_BYTES:
        raise MiraiRuntimeError(f"relatório excede 2 MB: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MiraiRuntimeError(f"relatório inválido {path.name}: {error}") from error
    if not isinstance(payload, dict):
        raise MiraiRuntimeError(f"relatório possui formato incompatível: {path.name}")
    run_id = payload.get("run_id")
    status = payload.get("status")
    project = payload.get("project")
    if (
        payload.get("schema_version") != 1
        or not isinstance(run_id, str)
        or not RUN_ID_PATTERN.fullmatch(run_id)
        or status not in VALID_STATUSES
        or not isinstance(project, dict)
        or not isinstance(project.get("name"), str)
    ):
        raise MiraiRuntimeError(f"relatório possui schema incompatível: {path.name}")
    return payload


def list_pilot_history(
    directory: Path,
    *,
    limit: int = 20,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Lê relatórios válidos, ordenados pelo horário declarado do piloto."""
    if limit <= 0 or limit > 10_000:
        raise MiraiRuntimeError("limit deve estar entre 1 e 10000")
    if status is not None and status not in {"passed", "failed"}:
        raise MiraiRuntimeError("status deve ser passed ou failed")
    root = directory.expanduser().resolve()
    if not root.exists():
        return []
    if not root.is_dir():
        raise MiraiRuntimeError(f"histórico não é um diretório: {root}")
    entries: list[dict[str, Any]] = []
    for path in root.glob("*.json"):
        payload = _load_report(path)
        if status is not None and payload["status"] != status:
            continue
        entries.append(
            {
                "run_id": payload["run_id"],
                "project": payload["project"]["name"],
                "device": payload["project"].get("device"),
                "status": payload["status"],
                "started_at": payload.get("started_at"),
                "finished_at": payload.get("finished_at"),
                "report_json": path,
                "report_markdown": path.with_suffix(".md"),
                "signature": path.with_name(path.name + ".sig"),
            }
        )
    entries.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return entries[:limit]


def get_pilot_report(directory: Path, run_id: str) -> dict[str, Any]:
    """Localiza um run_id exato sem aceitar o nome como caminho."""
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise MiraiRuntimeError("run_id inválido")
    matches = [
        path
        for path in directory.expanduser().resolve().glob(f"*-{run_id}.json")
        if path.is_file()
    ]
    if not matches:
        raise MiraiRuntimeError(f"piloto não encontrado: {run_id}")
    if len(matches) > 1:
        raise MiraiRuntimeError(f"run_id duplicado no histórico: {run_id}")
    return _load_report(matches[0])


def prune_pilot_history(
    directory: Path,
    *,
    keep: int,
    apply: bool = False,
) -> list[Path]:
    """Mantém os N relatórios mais novos e remove somente irmãos conhecidos."""
    if keep < 0:
        raise MiraiRuntimeError("keep não pode ser negativo")
    entries = list_pilot_history(directory, limit=10_000)
    candidates: list[Path] = []
    for entry in entries[keep:]:
        json_path = Path(entry["report_json"])
        for sibling in (
            json_path,
            Path(entry["report_markdown"]),
            Path(entry["signature"]),
        ):
            if sibling.is_file():
                candidates.append(sibling)
    if apply:
        for candidate in candidates:
            try:
                candidate.unlink()
            except OSError as error:
                raise MiraiRuntimeError(
                    f"não foi possível remover {candidate.name}: {error}"
                ) from error
    return candidates


def parse_report_time(value: str) -> datetime:
    """Converte ISO 8601 para consumidores que precisam calcular retenção."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise MiraiRuntimeError("data do relatório é inválida") from error
