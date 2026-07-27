"""Cliente HTTP para comunicação com o Mirai Agent."""

from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from .devices import Device
from .errors import MiraiRuntimeError
from .inspect import ensure_model_path, validate_model


DEFAULT_AGENT_TIMEOUT = 15.0


def _connection(device: Device) -> tuple[http.client.HTTPConnection, str]:
    parsed = urlsplit(device.url)
    connection_class: type[http.client.HTTPConnection]
    connection_class = (
        http.client.HTTPSConnection
        if parsed.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_class(
        parsed.hostname,
        parsed.port,
        timeout=DEFAULT_AGENT_TIMEOUT,
    )
    return connection, parsed.path.rstrip("/")


def _decode_response(
    device: Device,
    response: http.client.HTTPResponse,
) -> dict[str, Any]:
    raw_body = response.read()
    try:
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MiraiRuntimeError(
            f"Agent '{device.name}' retornou uma resposta inválida"
        ) from error

    if not isinstance(payload, dict):
        raise MiraiRuntimeError(
            f"Agent '{device.name}' retornou uma resposta inválida"
        )
    if response.status >= 400:
        message = payload.get("error") or f"HTTP {response.status}"
        raise MiraiRuntimeError(f"Agent '{device.name}': {message}")
    return payload


def request_json(
    device: Device,
    path: str,
    *,
    method: str = "GET",
) -> dict[str, Any]:
    """Executa uma requisição JSON simples contra o Agent."""
    connection, prefix = _connection(device)
    try:
        connection.request(
            method,
            f"{prefix}{path}",
            headers={"Accept": "application/json"},
        )
        return _decode_response(device, connection.getresponse())
    except (OSError, TimeoutError, http.client.HTTPException) as error:
        raise MiraiRuntimeError(
            f"não foi possível conectar ao Agent '{device.name}' em {device.url}"
        ) from error
    finally:
        connection.close()


def get_agent_info(device: Device) -> dict[str, Any]:
    """Retorna capacidades básicas do dispositivo remoto."""
    return request_json(device, "/v1/info")


def get_agent_logs(device: Device, limit: int = 20) -> list[dict[str, Any]]:
    """Retorna os eventos mais recentes registrados pelo Agent."""
    query = urlencode({"limit": limit})
    payload = request_json(device, f"/v1/logs?{query}")
    events = payload.get("events")
    if not isinstance(events, list):
        raise MiraiRuntimeError(
            f"Agent '{device.name}' retornou logs em formato inválido"
        )
    return events


def calculate_sha256(path: Path) -> str:
    """Calcula SHA-256 sem carregar o arquivo inteiro em memória."""
    digest = hashlib.sha256()
    with path.open("rb") as model_file:
        for chunk in iter(lambda: model_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def deploy_model(device: Device, model_path: Path) -> dict[str, Any]:
    """Valida e envia um modelo ao Agent usando um corpo binário."""
    ensure_model_path(model_path)
    validate_model(model_path)
    model_size = model_path.stat().st_size
    model_sha256 = calculate_sha256(model_path)
    connection, prefix = _connection(device)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/octet-stream",
        "Content-Length": str(model_size),
        "X-Mirai-Model-Name": model_path.name,
        "X-Mirai-SHA256": model_sha256,
    }

    try:
        with model_path.open("rb") as model_file:
            connection.request(
                "POST",
                f"{prefix}/v1/deployments",
                body=model_file,
                headers=headers,
            )
            return _decode_response(device, connection.getresponse())
    except (OSError, TimeoutError, http.client.HTTPException) as error:
        raise MiraiRuntimeError(
            f"falha ao enviar o modelo para o Agent '{device.name}'"
        ) from error
    finally:
        connection.close()
