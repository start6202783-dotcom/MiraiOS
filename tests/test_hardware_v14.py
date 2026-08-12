"""Testes da identidade privacy-safe de hardware da v0.14."""

from __future__ import annotations

from mirai.hardware import collect_hardware_identity


def test_hardware_identity_excludes_personal_identifiers() -> None:
    profile = collect_hardware_identity(
        providers=["CPUExecutionProvider"],
        overrides={
            "system": "Linux",
            "release": "6.8.0",
            "machine": "aarch64",
            "board_model": "Orange Pi Prime",
            "processor": "Cortex-A53",
            "cpu_count": 4,
            "memory_bytes": 2 * 1024**3,
        },
    )

    assert profile["architecture_class"] == "arm64"
    assert profile["hardware_id"].startswith("hw-")
    assert profile["environment_id"].startswith("env-")
    assert profile["privacy"]["hostname_collected"] is False
    assert "hostname" not in profile
    assert "serial_number" not in profile
    assert "mac_address" not in profile
    assert "ip_address" not in profile


def test_hardware_id_uses_memory_bucket_but_environment_tracks_release() -> None:
    base = {
        "system": "Linux",
        "machine": "x86_64",
        "board_model": "Mini PC",
        "processor": "Example CPU",
        "cpu_count": 4,
        "memory_bytes": 3 * 1024**3,
    }
    first = collect_hardware_identity(overrides={**base, "release": "6.8.0"})
    second = collect_hardware_identity(
        overrides={
            **base,
            "release": "6.9.0",
            "memory_bytes": 3 * 1024**3 + 128 * 1024**2,
        }
    )

    assert first["hardware_id"] == second["hardware_id"]
    assert first["environment_id"] != second["environment_id"]
