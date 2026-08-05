"""Ancoragem externa dos heads de auditoria em um control plane local."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_client import get_agent_audit, get_agent_health
from .audit import AUDIT_GENESIS_HASH, AUDIT_HASH_PATTERN, AUDIT_VERSION, AuditLog
from .devices import Device
from .errors import MiraiRuntimeError

ANCHOR_EVENT_VERSION = 1
MAX_ANCHOR_HISTORY = 100_000
MAX_ANCHOR_RETRIES = 3
AGENT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
DEFAULT_ANCHOR_PATH = Path(".mirai") / "control-plane" / "anchors.jsonl"
_ANCHOR_LOCK = threading.Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_anchor(ledger: AuditLog, agent_id: str) -> dict[str, Any] | None:
    for event in ledger.recent(MAX_ANCHOR_HISTORY):
        if event.get("type") == "audit_anchor" and event.get("agent_id") == agent_id:
            return event
    return None


def _validate_audit_status(status: dict[str, Any]) -> tuple[int, str]:
    records = status.get("records")
    head = status.get("head")
    if (
        status.get("valid") is not True
        or status.get("version") != AUDIT_VERSION
        or isinstance(records, bool)
        or not isinstance(records, int)
        or records < 0
        or not isinstance(head, str)
        or not AUDIT_HASH_PATTERN.fullmatch(head)
    ):
        raise MiraiRuntimeError("Agent retornou checkpoint de auditoria inválido")
    if records == 0 and head != AUDIT_GENESIS_HASH:
        raise MiraiRuntimeError("auditoria vazia retornou head diferente do gênesis")
    return records, head


def _checkpoint_identity(event: dict[str, Any] | None) -> tuple[object, object]:
    if event is None:
        return None, None
    return event.get("records"), event.get("head")


def _verify_extension(
    previous_records: int,
    previous_head: str,
    status: dict[str, Any],
) -> None:
    records, head = _validate_audit_status(status)
    proof = status.get("proof")
    if (
        status.get("from_records") != previous_records
        or status.get("from_head") != previous_head
        or not isinstance(proof, list)
        or len(proof) != records - previous_records
    ):
        raise MiraiRuntimeError("prova de extensão da auditoria está incompleta")
    expected_previous = previous_head
    for expected_sequence, item in enumerate(proof, start=previous_records + 1):
        if (
            not isinstance(item, dict)
            or set(item) != {"sequence", "previous_hash", "record_hash"}
            or item.get("sequence") != expected_sequence
            or item.get("previous_hash") != expected_previous
            or not isinstance(item.get("record_hash"), str)
            or not AUDIT_HASH_PATTERN.fullmatch(str(item["record_hash"]))
        ):
            raise MiraiRuntimeError("prova de extensão da auditoria não confere")
        expected_previous = str(item["record_hash"])
    if expected_previous != head:
        raise MiraiRuntimeError("prova de extensão não alcança o head atual")


def anchor_device(
    device: Device,
    *,
    ledger_path: Path = DEFAULT_ANCHOR_PATH,
    health: Callable[[Device], dict[str, Any]] = get_agent_health,
    audit: Callable[..., dict[str, Any]] = get_agent_audit,
) -> dict[str, Any]:
    """Ancora um Agent e recusa regressão ou reescrita da cadeia conhecida."""
    health_status = health(device)
    agent_id = health_status.get("agent_id")
    if not isinstance(agent_id, str) or not AGENT_ID_PATTERN.fullmatch(agent_id):
        raise MiraiRuntimeError(f"Agent '{device.name}' retornou identidade inválida")
    if device.agent_id is not None and device.agent_id != agent_id:
        raise MiraiRuntimeError(f"identidade do Agent '{device.name}' diverge do pareamento")

    resolved_ledger = ledger_path.expanduser().resolve()
    for _ in range(MAX_ANCHOR_RETRIES):
        with _ANCHOR_LOCK:
            latest = _latest_anchor(AuditLog(resolved_ledger), agent_id)
        if latest is None:
            status = audit(device)
        else:
            previous_records = latest.get("records")
            previous_head = latest.get("head")
            if (
                isinstance(previous_records, bool)
                or not isinstance(previous_records, int)
                or not isinstance(previous_head, str)
            ):
                raise MiraiRuntimeError("ledger externo contém âncora inválida")
            status = audit(
                device,
                from_records=previous_records,
                from_head=previous_head,
            )
            _verify_extension(previous_records, previous_head, status)

        records, head = _validate_audit_status(status)
        with _ANCHOR_LOCK:
            ledger = AuditLog(resolved_ledger)
            current = _latest_anchor(ledger, agent_id)
            if _checkpoint_identity(current) != _checkpoint_identity(latest):
                continue
            if current is not None and _checkpoint_identity(current) == (records, head):
                return {**current, "status": "unchanged"}

            event = {
                "version": ANCHOR_EVENT_VERSION,
                "type": "audit_anchor",
                "status": "anchored",
                "anchored_at": _utc_now(),
                "device": device.name,
                "agent_id": agent_id,
                "tls_fingerprint": device.tls_fingerprint,
                "records": records,
                "head": head,
            }
            anchor_record = ledger.append(event)
            return {
                **event,
                "ledger_sequence": anchor_record["sequence"],
                "ledger_head": anchor_record["record_hash"],
            }
    raise MiraiRuntimeError("a âncora mudou concorrentemente; tente novamente")


def anchor_fleet(
    devices: list[Device],
    *,
    ledger_path: Path = DEFAULT_ANCHOR_PATH,
    workers: int = 4,
    anchor: Callable[..., dict[str, Any]] = anchor_device,
) -> list[dict[str, Any]]:
    """Ancora uma frota em paralelo, preservando falhas por dispositivo."""
    if not 1 <= workers <= 32:
        raise MiraiRuntimeError("workers deve estar entre 1 e 32")
    if not devices:
        return []
    results: list[dict[str, Any]] = []

    def run(device: Device) -> dict[str, Any]:
        try:
            return anchor(device, ledger_path=ledger_path)
        except Exception as error:  # noqa: BLE001 - a frota mantém falhas parciais.
            return {
                "device": device.name,
                "status": "failed",
                "error": str(error),
            }

    with ThreadPoolExecutor(max_workers=min(workers, len(devices))) as executor:
        futures = {executor.submit(run, device): device for device in devices}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: str(item.get("device")))
    return results
