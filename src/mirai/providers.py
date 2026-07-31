"""Seleção explícita de execution providers e perfis de hardware."""

from __future__ import annotations

import platform
from importlib import metadata
from typing import Any

from .errors import MiraiRuntimeError

PROVIDER_PROFILES: dict[str, tuple[str, ...] | None] = {
    "auto": None,
    "cpu": ("CPUExecutionProvider",),
    "cuda": ("CUDAExecutionProvider", "CPUExecutionProvider"),
    "directml": ("DmlExecutionProvider", "CPUExecutionProvider"),
}
ACCELERATED_PROVIDERS = {
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
}


def normalize_provider_profile(profile: str) -> str:
    """Valida um perfil estável aceito pela CLI e pelo Agent."""
    normalized = profile.strip().lower()
    if normalized not in PROVIDER_PROFILES:
        available = ", ".join(PROVIDER_PROFILES)
        raise MiraiRuntimeError(
            f"perfil de provider desconhecido: {profile!r}; use {available}"
        )
    return normalized


def resolve_provider_profile(
    profile: str,
    available_providers: list[str] | tuple[str, ...],
) -> list[str]:
    """Resolve o perfil sem esconder a ausência de uma aceleração pedida."""
    normalized = normalize_provider_profile(profile)
    available = list(dict.fromkeys(available_providers))
    if normalized == "auto":
        preferred: list[str] = [
            provider
            for provider in (
                "CUDAExecutionProvider",
                "DmlExecutionProvider",
                "CPUExecutionProvider",
            )
            if provider in available
        ]
        if not preferred:
            raise MiraiRuntimeError(
                "nenhum execution provider compatível está disponível"
            )
        return preferred

    requested = PROVIDER_PROFILES[normalized]
    if requested is None:
        raise MiraiRuntimeError("perfil automático não foi resolvido")
    primary = requested[0]
    if primary not in available:
        raise MiraiRuntimeError(
            f"o perfil '{normalized}' exige {primary}, mas o runtime possui: "
            f"{', '.join(available) or 'nenhum'}"
        )
    return [provider for provider in requested if provider in available]


def hardware_profile(
    machine: str | None = None,
    system: str | None = None,
    providers: list[str] | None = None,
) -> dict[str, str]:
    """Classifica o host sem transformar detecção em promessa de suporte."""
    architecture = (machine or platform.machine()).lower()
    operating_system = (system or platform.system()).lower()
    provider_set = set(providers or [])

    if "CUDAExecutionProvider" in provider_set:
        profile = "nvidia-cuda"
    elif "DmlExecutionProvider" in provider_set:
        profile = "windows-directml"
    elif architecture in {"aarch64", "arm64"}:
        profile = "arm64-cpu"
    elif architecture.startswith("riscv"):
        profile = "riscv-experimental"
    else:
        profile = "cpu"

    support = "experimental" if architecture.startswith("riscv") else "validated"
    if operating_system not in {"linux", "windows", "darwin"}:
        support = "experimental"
    return {
        "profile": profile,
        "architecture": architecture or "unknown",
        "support": support,
    }


def list_runtime_backends() -> list[dict[str, Any]]:
    """Lista o backend ONNX e plugins experimentais via entry points."""
    backends: list[dict[str, Any]] = [
        {
            "name": "onnxruntime",
            "source": "builtin",
            "status": "stable",
        }
    ]
    try:
        discovered = metadata.entry_points(group="mirai.runtime_backends")
    except Exception as error:  # noqa: BLE001 - fronteira de plugins de terceiros.
        raise MiraiRuntimeError(
            f"não foi possível descobrir plugins de runtime: {error}"
        ) from error
    for entry_point in sorted(discovered, key=lambda item: item.name):
        backends.append(
            {
                "name": entry_point.name,
                "source": entry_point.value,
                "status": "experimental",
            }
        )
    return backends
