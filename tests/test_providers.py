"""Testes da seleção explícita de hardware e runtimes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from mirai.errors import MiraiRuntimeError
from mirai.providers import (
    hardware_profile,
    list_runtime_backends,
    normalize_provider_profile,
    resolve_provider_profile,
)
from mirai.runtime import create_session


@pytest.mark.parametrize(
    "profile,available,expected",
    [
        ("cpu", ["CPUExecutionProvider"], ["CPUExecutionProvider"]),
        (
            "cuda",
            ["CUDAExecutionProvider", "CPUExecutionProvider"],
            ["CUDAExecutionProvider", "CPUExecutionProvider"],
        ),
        ("cuda", ["CUDAExecutionProvider"], ["CUDAExecutionProvider"]),
        (
            "directml",
            ["DmlExecutionProvider", "CPUExecutionProvider"],
            ["DmlExecutionProvider", "CPUExecutionProvider"],
        ),
        (
            "auto",
            ["CPUExecutionProvider", "CUDAExecutionProvider"],
            ["CUDAExecutionProvider", "CPUExecutionProvider"],
        ),
        (
            "auto",
            ["DmlExecutionProvider", "CPUExecutionProvider"],
            ["DmlExecutionProvider", "CPUExecutionProvider"],
        ),
    ],
)
def test_resolve_provider_profiles(
    profile: str,
    available: list[str],
    expected: list[str],
) -> None:
    assert resolve_provider_profile(profile, available) == expected


@pytest.mark.parametrize(
    "profile,available,required",
    [
        ("cpu", ["CUDAExecutionProvider"], "CPUExecutionProvider"),
        ("cuda", ["CPUExecutionProvider"], "CUDAExecutionProvider"),
        ("directml", ["CPUExecutionProvider"], "DmlExecutionProvider"),
    ],
)
def test_explicit_provider_never_silently_falls_back(
    profile: str,
    available: list[str],
    required: str,
) -> None:
    with pytest.raises(MiraiRuntimeError, match=required):
        resolve_provider_profile(profile, available)


def test_auto_requires_at_least_one_supported_provider() -> None:
    with pytest.raises(MiraiRuntimeError, match="nenhum execution provider"):
        resolve_provider_profile("auto", ["CustomProvider"])


@pytest.mark.parametrize("profile", ["", "gpu", "tensorrt", "../cpu"])
def test_provider_profile_rejects_unknown_values(profile: str) -> None:
    with pytest.raises(MiraiRuntimeError, match="perfil de provider desconhecido"):
        normalize_provider_profile(profile)


@pytest.mark.parametrize(
    "machine,system,providers,expected,support",
    [
        ("x86_64", "Linux", ["CPUExecutionProvider"], "cpu", "validated"),
        ("aarch64", "Linux", ["CPUExecutionProvider"], "arm64-cpu", "validated"),
        ("arm64", "Darwin", ["CPUExecutionProvider"], "arm64-cpu", "validated"),
        ("riscv64", "Linux", ["CPUExecutionProvider"], "riscv-experimental", "experimental"),
        ("x86_64", "Linux", ["CUDAExecutionProvider"], "nvidia-cuda", "validated"),
        ("AMD64", "Windows", ["DmlExecutionProvider"], "windows-directml", "validated"),
    ],
)
def test_hardware_profile_is_honest_about_support(
    machine: str,
    system: str,
    providers: list[str],
    expected: str,
    support: str,
) -> None:
    result = hardware_profile(machine, system, providers)
    assert result["profile"] == expected
    assert result["support"] == support


def test_runtime_backend_list_contains_builtin() -> None:
    assert list_runtime_backends()[0] == {
        "name": "onnxruntime",
        "source": "builtin",
        "status": "stable",
    }


def test_runtime_backend_list_discovers_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = SimpleNamespace(name="llama-cpp", value="plugin.module:Backend")
    monkeypatch.setattr(
        "mirai.providers.metadata.entry_points",
        lambda **kwargs: [fake],
    )

    backends = list_runtime_backends()

    assert backends[1]["name"] == "llama-cpp"
    assert backends[1]["status"] == "experimental"


def test_create_session_applies_directml_safety_options(dummy_model: Path) -> None:
    captured: dict[str, object] = {}

    class Options:
        enable_mem_pattern = True
        execution_mode: object = None

    class Runtime:
        SessionOptions = Options
        ExecutionMode = SimpleNamespace(ORT_SEQUENTIAL="sequential")

        @staticmethod
        def get_available_providers() -> list[str]:
            return ["DmlExecutionProvider", "CPUExecutionProvider"]

        @staticmethod
        def InferenceSession(path: str, **kwargs: object) -> object:
            captured.update(kwargs)
            return object()

    create_session(dummy_model, Runtime, "directml")

    assert captured["providers"] == ["DmlExecutionProvider", "CPUExecutionProvider"]
    options = captured["sess_options"]
    assert isinstance(options, Options)
    assert options.enable_mem_pattern is False
    assert options.execution_mode == "sequential"
