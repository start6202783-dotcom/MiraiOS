"""Testes do registro local de dispositivos."""

from __future__ import annotations

from pathlib import Path

import pytest

from mirai.devices import (
    add_device,
    get_device,
    load_devices,
    remove_device,
)
from mirai.errors import MiraiRuntimeError


def test_device_registry_round_trip(tmp_path: Path) -> None:
    registry = tmp_path / "devices.json"

    device = add_device(
        "lab-local",
        "http://127.0.0.1:8080/",
        path=registry,
    )

    assert device.url == "http://127.0.0.1:8080"
    assert get_device("lab-local", path=registry) == device
    assert list(load_devices(registry)) == ["lab-local"]

    assert remove_device("lab-local", path=registry) == device
    assert load_devices(registry) == {}


def test_device_registry_rejects_duplicate(tmp_path: Path) -> None:
    registry = tmp_path / "devices.json"
    add_device("local", "http://127.0.0.1:8080", path=registry)

    with pytest.raises(MiraiRuntimeError, match="já está cadastrado"):
        add_device("local", "http://127.0.0.1:9090", path=registry)

    replaced = add_device(
        "local",
        "http://127.0.0.1:9090",
        replace=True,
        path=registry,
    )
    assert replaced.url == "http://127.0.0.1:9090"


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("nome com espaço", "http://127.0.0.1:8080"),
        ("local", "ftp://127.0.0.1"),
        ("local", "http://user:secret@127.0.0.1"),
        ("local", "http://127.0.0.1:8080/api"),
    ],
)
def test_device_registry_rejects_unsafe_values(
    tmp_path: Path,
    name: str,
    url: str,
) -> None:
    with pytest.raises(MiraiRuntimeError):
        add_device(name, url, path=tmp_path / "devices.json")


def test_device_registry_reports_corrupted_file(tmp_path: Path) -> None:
    registry = tmp_path / "devices.json"
    registry.write_text("{não-json", encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="não foi possível ler"):
        load_devices(registry)
