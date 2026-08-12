"""Identidade de hardware sem coletar identificadores pessoais do host."""

from __future__ import annotations

import hashlib
import os
import platform
import re
from pathlib import Path
from typing import Any

from . import __version__
from .json_codec import canonical_json_bytes

HARDWARE_PROFILE_VERSION = 1
MAX_HARDWARE_LABEL_CHARS = 160
_SPACE_PATTERN = re.compile(r"\s+")


def _clean_label(value: object, *, fallback: str = "unknown") -> str:
    text = _SPACE_PATTERN.sub(" ", str(value or "")).strip()
    return text[:MAX_HARDWARE_LABEL_CHARS] or fallback


def _read_first(paths: tuple[Path, ...]) -> str | None:
    for path in paths:
        try:
            content = path.read_bytes().replace(b"\x00", b"").decode(
                "utf-8", errors="replace"
            )
        except OSError:
            continue
        cleaned = _clean_label(content, fallback="")
        if cleaned:
            return cleaned
    return None


def _linux_cpu_model() -> str | None:
    try:
        lines = Path("/proc/cpuinfo").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return None
    discovered: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition(":")
        normalized_key = key.strip().lower()
        if separator and normalized_key in {
            "model name",
            "hardware",
            "cpu model",
            "uarch",
            "processor",
        } and value.strip():
            discovered.setdefault(normalized_key, _clean_label(value))
    for key in ("model name", "hardware", "cpu model", "uarch", "processor"):
        candidate = discovered.get(key)
        if candidate and (key != "processor" or not candidate.isdecimal()):
            return candidate
    return None


def _memory_bytes() -> int | None:
    if platform.system().lower() == "linux":
        try:
            for line in Path("/proc/meminfo").read_text(
                encoding="ascii", errors="replace"
            ).splitlines():
                if line.startswith("MemTotal:"):
                    kib = int(line.split()[1])
                    return kib * 1024
        except (OSError, ValueError, IndexError):
            pass
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        pages = int(os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    total = page_size * pages
    return total if total > 0 else None


def _memory_bucket(memory_bytes: int | None) -> int | None:
    if memory_bytes is None:
        return None
    gib = 1024**3
    buckets = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024)
    for bucket in buckets:
        if memory_bytes <= bucket * gib:
            return bucket * gib
    return 2048 * gib


def _architecture_class(machine: str) -> str:
    normalized = machine.lower()
    if normalized in {"aarch64", "arm64"}:
        return "arm64"
    if normalized.startswith(("armv7", "armv8")):
        return "arm32"
    if normalized in {"x86_64", "amd64"}:
        return "x86-64"
    if normalized.startswith("riscv"):
        return "riscv-experimental"
    return "unclassified"


def _digest_id(prefix: str, value: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json_bytes(value)).hexdigest()[:24]
    return f"{prefix}-{digest}"


def collect_hardware_identity(
    *,
    providers: list[str] | tuple[str, ...] | None = None,
    category: str | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Coleta um perfil comparável sem hostname, MAC, IP ou número de série."""
    supplied = dict(overrides or {})
    system = _clean_label(supplied.get("system", platform.system())).lower()
    release = _clean_label(supplied.get("release", platform.release()))
    machine = _clean_label(supplied.get("machine", platform.machine())).lower()
    board_model = _clean_label(
        supplied.get(
            "board_model",
            _read_first(
                (
                    Path("/proc/device-tree/model"),
                    Path("/sys/firmware/devicetree/base/model"),
                )
            ),
        )
    )
    processor = _clean_label(
        supplied.get("processor", _linux_cpu_model() or platform.processor())
    )
    cpu_count_raw = supplied.get("cpu_count", os.cpu_count())
    cpu_count = (
        int(cpu_count_raw)
        if isinstance(cpu_count_raw, int) and not isinstance(cpu_count_raw, bool)
        and cpu_count_raw > 0
        else None
    )
    memory_raw = supplied.get("memory_bytes", _memory_bytes())
    memory_bytes = (
        int(memory_raw)
        if isinstance(memory_raw, int) and not isinstance(memory_raw, bool)
        and memory_raw > 0
        else None
    )
    provider_names = sorted(
        {
            _clean_label(item)
            for item in (providers or supplied.get("providers") or [])
            if isinstance(item, str) and item.strip()
        }
    )
    hardware_subject = {
        "architecture": machine,
        "board_model": board_model,
        "processor": processor,
        "cpu_count": cpu_count,
        "memory_bucket_bytes": _memory_bucket(memory_bytes),
    }
    environment_subject = {
        **hardware_subject,
        "system": system,
        "release": release,
        "python": platform.python_version(),
        "miraios": __version__,
        "providers": provider_names,
    }
    return {
        "schema_version": HARDWARE_PROFILE_VERSION,
        "hardware_id": _digest_id("hw", hardware_subject),
        "environment_id": _digest_id("env", environment_subject),
        "category": _clean_label(category, fallback="unclassified").lower(),
        "category_source": "user" if category else "automatic",
        "architecture_class": _architecture_class(machine),
        "system": system,
        "release": release,
        "machine": machine,
        "board_model": board_model,
        "processor": processor,
        "cpu_count": cpu_count,
        "memory_bytes": memory_bytes,
        "providers": provider_names,
        "privacy": {
            "hostname_collected": False,
            "network_identifiers_collected": False,
            "serial_numbers_collected": False,
        },
    }
