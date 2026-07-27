"""Registro local de dispositivos gerenciados pelo MiraiOS."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .errors import MiraiRuntimeError


DEVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


@dataclass(frozen=True, slots=True)
class Device:
    """Representa um Mirai Agent conhecido pela CLI."""

    name: str
    url: str


def mirai_home() -> Path:
    """Retorna o diretório de dados da CLI, permitindo isolamento em testes."""
    configured_home = os.environ.get("MIRAI_HOME")
    if configured_home:
        return Path(configured_home).expanduser()
    return Path.home() / ".mirai"


def device_registry_path() -> Path:
    """Retorna o caminho do registro persistente de dispositivos."""
    return mirai_home() / "devices.json"


def normalize_device_name(name: str) -> str:
    """Valida um identificador curto e seguro para o dispositivo."""
    if not DEVICE_NAME_PATTERN.fullmatch(name):
        raise MiraiRuntimeError(
            "nome de dispositivo inválido; use letras, números, ponto, "
            "hífen ou sublinhado (máximo de 64 caracteres)"
        )
    return name


def normalize_agent_url(url: str) -> str:
    """Normaliza uma URL HTTP(S) sem credenciais, query ou fragmento."""
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise MiraiRuntimeError("URL do Agent deve começar com http:// ou https://")
    if parsed.username or parsed.password:
        raise MiraiRuntimeError("URL do Agent não pode conter credenciais")
    if parsed.query or parsed.fragment:
        raise MiraiRuntimeError("URL do Agent não pode conter query ou fragmento")
    if parsed.path not in {"", "/"}:
        raise MiraiRuntimeError("URL do Agent não pode conter um caminho")

    try:
        port = parsed.port
    except ValueError as error:
        raise MiraiRuntimeError("porta inválida na URL do Agent") from error

    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    return urlunsplit((parsed.scheme, netloc, "", "", ""))


def load_devices(path: Path | None = None) -> dict[str, Device]:
    """Carrega o registro, retornando um dicionário indexado pelo nome."""
    registry_path = path or device_registry_path()
    if not registry_path.exists():
        return {}

    try:
        raw_data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MiraiRuntimeError(
            f"não foi possível ler o registro de dispositivos: {error}"
        ) from error

    if not isinstance(raw_data, dict) or raw_data.get("version") != 1:
        raise MiraiRuntimeError("registro de dispositivos possui formato incompatível")

    raw_devices = raw_data.get("devices")
    if not isinstance(raw_devices, list):
        raise MiraiRuntimeError("registro de dispositivos está corrompido")

    devices: dict[str, Device] = {}
    try:
        for item in raw_devices:
            name = normalize_device_name(item["name"])
            devices[name] = Device(
                name=name,
                url=normalize_agent_url(item["url"]),
            )
    except (KeyError, TypeError, MiraiRuntimeError) as error:
        raise MiraiRuntimeError("registro de dispositivos está corrompido") from error
    return devices


def save_devices(
    devices: dict[str, Device],
    path: Path | None = None,
) -> None:
    """Persiste o registro de forma atômica."""
    registry_path = path or device_registry_path()
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "devices": [
            asdict(device)
            for device in sorted(devices.values(), key=lambda item: item.name)
        ],
    }
    temporary_path = registry_path.with_suffix(".tmp")
    try:
        temporary_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(registry_path)
    except OSError as error:
        raise MiraiRuntimeError(
            f"não foi possível salvar o registro de dispositivos: {error}"
        ) from error


def add_device(
    name: str,
    url: str,
    *,
    replace: bool = False,
    path: Path | None = None,
) -> Device:
    """Adiciona um Agent ao registro local."""
    normalized_name = normalize_device_name(name)
    device = Device(
        name=normalized_name,
        url=normalize_agent_url(url),
    )
    devices = load_devices(path)
    if normalized_name in devices and not replace:
        raise MiraiRuntimeError(
            f"dispositivo '{normalized_name}' já está cadastrado"
        )
    devices[normalized_name] = device
    save_devices(devices, path)
    return device


def get_device(name: str, path: Path | None = None) -> Device:
    """Retorna um dispositivo cadastrado ou um erro controlado."""
    normalized_name = normalize_device_name(name)
    devices = load_devices(path)
    try:
        return devices[normalized_name]
    except KeyError as error:
        raise MiraiRuntimeError(
            f"dispositivo '{normalized_name}' não está cadastrado"
        ) from error


def remove_device(name: str, path: Path | None = None) -> Device:
    """Remove um dispositivo do registro."""
    normalized_name = normalize_device_name(name)
    devices = load_devices(path)
    try:
        device = devices.pop(normalized_name)
    except KeyError as error:
        raise MiraiRuntimeError(
            f"dispositivo '{normalized_name}' não está cadastrado"
        ) from error
    save_devices(devices, path)
    return device
