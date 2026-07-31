"""Registro local de dispositivos gerenciados pelo MiraiOS."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .errors import MiraiRuntimeError
from .security import ACCESS_ROLES, normalize_fingerprint

DEVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
AGENT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
CLIENT_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
DEVICE_REGISTRY_VERSION = 3


@dataclass(frozen=True, slots=True)
class Device:
    """Representa um Mirai Agent conhecido pela CLI."""

    name: str
    url: str
    token: str | None = None
    tls_fingerprint: str | None = None
    agent_id: str | None = None
    client_id: str | None = None
    role: str | None = None

    @property
    def paired(self) -> bool:
        """Informa se o dispositivo possui credenciais do Hikari Link."""
        return bool(self.token and self.tls_fingerprint)


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


def is_loopback_agent_url(url: str) -> bool:
    """Informa se uma URL aponta apenas para a máquina local."""
    parsed = urlsplit(normalize_agent_url(url))
    hostname = (parsed.hostname or "").lower()
    if hostname == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _normalize_optional_credentials(
    *,
    url: str,
    token: str | None,
    tls_fingerprint: str | None,
    agent_id: str | None,
    client_id: str | None,
    role: str | None,
    allow_legacy_unpaired: bool = False,
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    provided = (token, tls_fingerprint, agent_id, client_id)
    if not any(value is not None for value in provided):
        if allow_legacy_unpaired:
            return None, None, None, None, None
        if not is_loopback_agent_url(url):
            raise MiraiRuntimeError(
                "Agents fora de localhost exigem pareamento; "
                "use 'mirai device pair'"
            )
        if urlsplit(url).scheme == "https":
            raise MiraiRuntimeError(
                "uma URL HTTPS exige fingerprint e credenciais de pareamento"
            )
        return None, None, None, None, None

    if not all(isinstance(value, str) and value for value in provided):
        raise MiraiRuntimeError(
            "credenciais do dispositivo estão incompletas"
        )
    if (
        not isinstance(token, str)
        or not isinstance(tls_fingerprint, str)
        or not isinstance(agent_id, str)
        or not isinstance(client_id, str)
    ):
        raise MiraiRuntimeError("credenciais do dispositivo estão incompletas")
    if urlsplit(url).scheme != "https":
        raise MiraiRuntimeError(
            "credenciais de pareamento só podem ser usadas com HTTPS"
        )
    if not TOKEN_PATTERN.fullmatch(token):
        raise MiraiRuntimeError("token do dispositivo possui formato inválido")
    normalized_fingerprint = normalize_fingerprint(tls_fingerprint)
    if not AGENT_ID_PATTERN.fullmatch(agent_id):
        raise MiraiRuntimeError("identidade do Agent possui formato inválido")
    if not CLIENT_ID_PATTERN.fullmatch(client_id):
        raise MiraiRuntimeError("identidade do cliente possui formato inválido")
    normalized_role = role or "admin"
    if normalized_role not in ACCESS_ROLES:
        raise MiraiRuntimeError("papel do cliente possui formato inválido")
    return token, normalized_fingerprint, agent_id, client_id, normalized_role


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

    if (
        not isinstance(raw_data, dict)
        or raw_data.get("version") not in {1, 2, DEVICE_REGISTRY_VERSION}
    ):
        raise MiraiRuntimeError("registro de dispositivos possui formato incompatível")

    raw_devices = raw_data.get("devices")
    if not isinstance(raw_devices, list):
        raise MiraiRuntimeError("registro de dispositivos está corrompido")

    devices: dict[str, Device] = {}
    legacy_registry = raw_data.get("version") == 1
    try:
        for item in raw_devices:
            name = normalize_device_name(item["name"])
            url = normalize_agent_url(item["url"])
            token, fingerprint, agent_id, client_id, role = (
                _normalize_optional_credentials(
                    url=url,
                    token=item.get("token"),
                    tls_fingerprint=item.get("tls_fingerprint"),
                    agent_id=item.get("agent_id"),
                    client_id=item.get("client_id"),
                    role=item.get("role"),
                    allow_legacy_unpaired=legacy_registry,
                )
            )
            devices[name] = Device(
                name=name,
                url=url,
                token=token,
                tls_fingerprint=fingerprint,
                agent_id=agent_id,
                client_id=client_id,
                role=role,
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
        "version": DEVICE_REGISTRY_VERSION,
        "devices": [
            asdict(device)
            for device in sorted(devices.values(), key=lambda item: item.name)
        ],
    }
    temporary_path = registry_path.with_name(f".{registry_path.name}.tmp")
    try:
        descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as registry_file:
            registry_file.write(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
            )
        os.replace(temporary_path, registry_path)
        try:
            os.chmod(registry_path, 0o600)
        except OSError:
            pass
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise MiraiRuntimeError(
            f"não foi possível salvar o registro de dispositivos: {error}"
        ) from error


def add_device(
    name: str,
    url: str,
    *,
    replace: bool = False,
    path: Path | None = None,
    token: str | None = None,
    tls_fingerprint: str | None = None,
    agent_id: str | None = None,
    client_id: str | None = None,
    role: str | None = None,
) -> Device:
    """Adiciona um Agent ao registro local."""
    normalized_name = normalize_device_name(name)
    normalized_url = normalize_agent_url(url)
    (
        normalized_token,
        normalized_fingerprint,
        normalized_agent_id,
        normalized_client_id,
        normalized_role,
    ) = _normalize_optional_credentials(
        url=normalized_url,
        token=token,
        tls_fingerprint=tls_fingerprint,
        agent_id=agent_id,
        client_id=client_id,
        role=role,
    )
    device = Device(
        name=normalized_name,
        url=normalized_url,
        token=normalized_token,
        tls_fingerprint=normalized_fingerprint,
        agent_id=normalized_agent_id,
        client_id=normalized_client_id,
        role=normalized_role,
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
