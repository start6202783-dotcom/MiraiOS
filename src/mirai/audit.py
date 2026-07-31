"""Log de auditoria encadeado para detectar edição, reordenação e corrupção."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

from .errors import MiraiRuntimeError
from .json_codec import canonical_json_bytes, strict_json_dumps, strict_json_loads
from .storage import atomic_write_text

AUDIT_VERSION = 1
AUDIT_GENESIS_HASH = "0" * 64
AUDIT_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_AUDIT_RECORD_BYTES = 256 * 1024
MAX_AUDIT_FILE_BYTES = 128 * 1024 * 1024


class AuditLog:
    """Ledger local append-only; o head pode ser ancorado fora do dispositivo."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.head_path = path.with_suffix(path.suffix + ".head")
        self._cached_status: dict[str, Any] | None = None
        self._cached_fingerprint: tuple[object, object] | None = None

    @staticmethod
    def _file_fingerprint(path: Path) -> tuple[int, int, int, int, int] | None:
        if not path.exists():
            return None
        try:
            info = path.stat()
        except OSError as error:
            raise MiraiRuntimeError(
                f"não foi possível inspecionar a auditoria: {error}"
            ) from error
        return (
            info.st_dev,
            info.st_ino,
            info.st_size,
            info.st_mtime_ns,
            info.st_ctime_ns,
        )

    def _state_fingerprint(self) -> tuple[object, object]:
        return (
            self._file_fingerprint(self.path),
            self._file_fingerprint(self.head_path),
        )

    @staticmethod
    def _record_hash(record_without_hash: dict[str, Any]) -> str:
        return hashlib.sha256(canonical_json_bytes(record_without_hash)).hexdigest()

    def _records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            size = self.path.stat().st_size
            if size > MAX_AUDIT_FILE_BYTES:
                raise MiraiRuntimeError("log de auditoria excede o limite de 128 MB")
            raw_lines = self.path.read_bytes().splitlines()
        except OSError as error:
            raise MiraiRuntimeError(f"não foi possível ler a auditoria: {error}") from error
        records: list[dict[str, Any]] = []
        for index, line in enumerate(raw_lines, start=1):
            if not line or len(line) > MAX_AUDIT_RECORD_BYTES:
                raise MiraiRuntimeError(
                    f"registro de auditoria {index} vazio ou grande demais"
                )
            value = strict_json_loads(line, label=f"registro de auditoria {index}")
            if not isinstance(value, dict):
                raise MiraiRuntimeError(f"registro de auditoria {index} não é objeto")
            records.append(value)
        return records

    def verify(self) -> dict[str, Any]:
        """Valida sequência, hash anterior e hash do conteúdo de cada registro."""
        records = self._records()
        previous = AUDIT_GENESIS_HASH
        for expected_sequence, record in enumerate(records, start=1):
            if set(record) != {
                "version",
                "sequence",
                "previous_hash",
                "event",
                "record_hash",
            }:
                raise MiraiRuntimeError(
                    f"registro de auditoria {expected_sequence} possui campos inválidos"
                )
            if (
                record["version"] != AUDIT_VERSION
                or record["sequence"] != expected_sequence
                or record["previous_hash"] != previous
                or not isinstance(record["event"], dict)
                or not isinstance(record["record_hash"], str)
                or not AUDIT_HASH_PATTERN.fullmatch(record["record_hash"])
            ):
                raise MiraiRuntimeError(
                    f"cadeia de auditoria inválida no registro {expected_sequence}"
                )
            core = {key: value for key, value in record.items() if key != "record_hash"}
            actual = self._record_hash(core)
            if actual != record["record_hash"]:
                raise MiraiRuntimeError(
                    f"hash da auditoria não confere no registro {expected_sequence}"
                )
            previous = actual
        status = {
            "valid": True,
            "version": AUDIT_VERSION,
            "records": len(records),
            "head": previous,
        }
        self._verify_checkpoint(status, records)
        self._cached_status = dict(status)
        self._cached_fingerprint = self._state_fingerprint()
        return status

    def _verified_status_for_append(self) -> dict[str, Any]:
        if (
            self._cached_status is not None
            and self._cached_fingerprint == self._state_fingerprint()
        ):
            return dict(self._cached_status)
        return self.verify()

    def _verify_checkpoint(
        self,
        status: dict[str, Any],
        records: list[dict[str, Any]],
    ) -> None:
        if not self.head_path.exists():
            if records:
                raise MiraiRuntimeError("checkpoint da auditoria está ausente")
            return
        try:
            checkpoint = strict_json_loads(
                self.head_path.read_bytes(),
                label="checkpoint da auditoria",
            )
        except OSError as error:
            raise MiraiRuntimeError(
                f"não foi possível ler o checkpoint da auditoria: {error}"
            ) from error
        if (
            not isinstance(checkpoint, dict)
            or set(checkpoint) != {"version", "records", "head"}
            or checkpoint.get("version") != AUDIT_VERSION
            or not isinstance(checkpoint.get("records"), int)
            or not isinstance(checkpoint.get("head"), str)
        ):
            raise MiraiRuntimeError("checkpoint da auditoria possui formato inválido")
        checkpoint_records = int(checkpoint["records"])
        actual_records = int(status["records"])
        if checkpoint_records == actual_records:
            if checkpoint["head"] != status["head"]:
                raise MiraiRuntimeError("checkpoint da auditoria não confere")
            return
        if (
            checkpoint_records == actual_records - 1
            and records
            and records[-1]["previous_hash"] == checkpoint["head"]
        ):
            self._write_checkpoint(status)
            return
        raise MiraiRuntimeError("auditoria foi truncada ou divergiu do checkpoint")

    def _write_checkpoint(self, status: dict[str, Any]) -> None:
        checkpoint = {
            "version": AUDIT_VERSION,
            "records": int(status["records"]),
            "head": str(status["head"]),
        }
        atomic_write_text(
            self.head_path,
            strict_json_dumps(checkpoint, sort_keys=True) + "\n",
            mode=0o600,
        )

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        """Recusa continuar uma cadeia corrompida e sincroniza o novo registro."""
        status = self._verified_status_for_append()
        core = {
            "version": AUDIT_VERSION,
            "sequence": int(status["records"]) + 1,
            "previous_hash": str(status["head"]),
            "event": event,
        }
        record = {**core, "record_hash": self._record_hash(core)}
        encoded = (strict_json_dumps(record, sort_keys=True) + "\n").encode("utf-8")
        if len(encoded) > MAX_AUDIT_RECORD_BYTES:
            raise MiraiRuntimeError("evento excede o limite do log de auditoria")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
            with os.fdopen(descriptor, "ab") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as error:
            raise MiraiRuntimeError(
                f"não foi possível registrar a auditoria: {error}"
            ) from error
        next_status = {
            "valid": True,
            "version": AUDIT_VERSION,
            "records": record["sequence"],
            "head": record["record_hash"],
        }
        self._write_checkpoint(next_status)
        self._cached_status = dict(next_status)
        self._cached_fingerprint = self._state_fingerprint()
        return record

    def recent(self, limit: int) -> list[dict[str, Any]]:
        self.verify()
        records = self._records()
        if self._cached_fingerprint != self._state_fingerprint():
            raise MiraiRuntimeError("auditoria foi alterada durante a leitura")
        return [dict(item["event"]) for item in reversed(records[-limit:])]
