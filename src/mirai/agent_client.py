"""Cliente HTTP para comunicação com o Mirai Agent."""

from __future__ import annotations

import base64
import hashlib
import http.client
import secrets
import ssl
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from . import __version__
from .admission import MAX_SIGNATURE_HEADER_CHARS
from .attachments import encode_remote_inputs
from .devices import (
    Device,
    add_device,
    is_loopback_agent_url,
    load_devices,
    normalize_agent_url,
    normalize_device_name,
)
from .errors import MiraiRuntimeError
from .inspect import validate_artifact
from .json_codec import strict_json_dumps, strict_json_loads
from .package import MIRAI_EXTENSION, MIRAI_MEDIA_TYPE, calculate_sha256
from .providers import normalize_provider_profile
from .security import normalize_fingerprint

DEFAULT_AGENT_TIMEOUT = 15.0


def _connection(device: Device) -> tuple[http.client.HTTPConnection, str]:
    parsed = urlsplit(device.url)
    hostname = parsed.hostname
    if hostname is None:
        raise MiraiRuntimeError(f"URL do dispositivo '{device.name}' não possui host")
    connection: http.client.HTTPConnection
    if parsed.scheme == "https":
        if not device.tls_fingerprint:
            raise MiraiRuntimeError(
                f"dispositivo '{device.name}' não possui fingerprint TLS"
            )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        connection = http.client.HTTPSConnection(
            hostname,
            parsed.port,
            timeout=DEFAULT_AGENT_TIMEOUT,
            context=context,
        )
        try:
            connection.connect()
            if connection.sock is None:
                raise MiraiRuntimeError(
                    f"Agent '{device.name}' não abriu um canal TLS"
                )
            certificate = connection.sock.getpeercert(binary_form=True)
            if certificate is None:
                raise MiraiRuntimeError(
                    f"Agent '{device.name}' não apresentou certificado TLS"
                )
        except (OSError, ssl.SSLError, http.client.HTTPException) as error:
            connection.close()
            raise MiraiRuntimeError(
                f"falha no canal TLS com o Agent '{device.name}'"
            ) from error
        actual_fingerprint = hashlib.sha256(certificate).hexdigest()
        expected_fingerprint = normalize_fingerprint(
            device.tls_fingerprint
        )
        if not secrets.compare_digest(
            actual_fingerprint,
            expected_fingerprint,
        ):
            connection.close()
            raise MiraiRuntimeError(
                f"fingerprint TLS do Agent '{device.name}' não confere"
            )
    else:
        if device.token or not is_loopback_agent_url(device.url):
            raise MiraiRuntimeError(
                "credenciais nunca podem ser enviadas por HTTP; "
                "use HTTPS com pareamento"
            )
        connection = http.client.HTTPConnection(
            hostname,
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
        payload = (
            strict_json_loads(raw_body, label="resposta do Agent")
            if raw_body
            else {}
        )
    except MiraiRuntimeError as error:
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
    payload: dict[str, Any] | None = None,
    authenticate: bool = True,
) -> dict[str, Any]:
    """Executa uma requisição JSON simples contra o Agent."""
    connection: http.client.HTTPConnection | None = None
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = strict_json_dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
        headers["Content-Length"] = str(len(body))
    if authenticate and device.token:
        headers["Authorization"] = f"Bearer {device.token}"
    try:
        connection, prefix = _connection(device)
        connection.request(
            method,
            f"{prefix}{path}",
            body=body,
            headers=headers,
        )
        return _decode_response(device, connection.getresponse())
    except (OSError, TimeoutError, http.client.HTTPException) as error:
        raise MiraiRuntimeError(
            f"não foi possível conectar ao Agent '{device.name}' em {device.url}"
        ) from error
    finally:
        if connection is not None:
            connection.close()


def get_agent_health(device: Device) -> dict[str, Any]:
    """Consulta o endpoint público usando o canal esperado do dispositivo."""
    return request_json(
        device,
        "/v1/health",
        authenticate=False,
    )


def pair_device(
    name: str,
    url: str,
    code: str,
    fingerprint: str,
    *,
    replace: bool = False,
) -> tuple[Device, dict[str, Any]]:
    """Pareia a CLI após fixar o certificado informado fora de banda."""
    normalized_name = normalize_device_name(name)
    normalized_url = normalize_agent_url(url)
    if normalized_name in load_devices() and not replace:
        raise MiraiRuntimeError(
            f"dispositivo '{normalized_name}' já está cadastrado; "
            "use --replace para substituir"
        )
    if urlsplit(normalized_url).scheme != "https":
        raise MiraiRuntimeError("pareamento exige uma URL HTTPS")
    expected_fingerprint = normalize_fingerprint(fingerprint)
    provisional = Device(
        name=normalized_name,
        url=normalized_url,
        tls_fingerprint=expected_fingerprint,
    )
    pairing = request_json(
        provisional,
        "/v1/pair",
        method="POST",
        payload={
            "name": normalized_name,
            "code": code,
        },
        authenticate=False,
    )
    required = {
        "token": pairing.get("token"),
        "fingerprint": pairing.get("fingerprint"),
        "agent_id": pairing.get("agent_id"),
        "client_id": pairing.get("client_id"),
        "role": pairing.get("role"),
    }
    if not all(isinstance(value, str) and value for value in required.values()):
        raise MiraiRuntimeError(
            f"Agent '{normalized_name}' retornou credenciais inválidas"
        )
    returned_fingerprint = normalize_fingerprint(
        str(required["fingerprint"])
    )
    if not secrets.compare_digest(
        returned_fingerprint,
        expected_fingerprint,
    ):
        raise MiraiRuntimeError(
            f"Agent '{normalized_name}' retornou outra identidade TLS"
        )
    device = add_device(
        normalized_name,
        normalized_url,
        replace=replace,
        token=str(required["token"]),
        tls_fingerprint=returned_fingerprint,
        agent_id=str(required["agent_id"]),
        client_id=str(required["client_id"]),
        role=str(required["role"]),
    )
    return device, pairing


def revoke_remote_device(device: Device) -> dict[str, Any]:
    """Revoga no Agent as credenciais usadas pela CLI atual."""
    if not device.paired:
        raise MiraiRuntimeError(
            f"dispositivo '{device.name}' não utiliza pareamento"
        )
    return request_json(
        device,
        "/v1/clients/self",
        method="DELETE",
    )


def doctor_device(device: Device) -> dict[str, Any]:
    """Executa os diagnósticos essenciais do canal e do runtime."""
    health = get_agent_health(device)
    info = get_agent_info(device)
    deployments = get_deployment_status(device)
    audit = get_agent_audit(device)
    agent_version = str(health.get("agent_version", ""))
    client_series = ".".join(__version__.split(".")[:2])
    agent_series = ".".join(agent_version.split(".")[:2])
    return {
        "health": health,
        "info": info,
        "deployments": deployments,
        "audit": audit,
        "tls": urlsplit(device.url).scheme == "https",
        "authenticated": device.paired,
        "compatible": bool(
            client_series
            and agent_series
            and client_series == agent_series
        ),
    }


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


def get_agent_audit(device: Device) -> dict[str, Any]:
    """Verifica a cadeia local e devolve o head para ancoragem externa."""
    payload = request_json(device, "/v1/audit")
    if payload.get("valid") is not True or not isinstance(payload.get("head"), str):
        raise MiraiRuntimeError(
            f"Agent '{device.name}' retornou auditoria inválida"
        )
    return payload


def get_deployment_status(device: Device) -> dict[str, Any]:
    """Retorna deployments conhecidos e o modelo ativo do Agent."""
    payload = request_json(device, "/v1/deployments")
    deployments = payload.get("deployments")
    if not isinstance(deployments, list):
        raise MiraiRuntimeError(
            f"Agent '{device.name}' retornou deployments inválidos"
        )
    return payload


def get_agent_clients(device: Device) -> list[dict[str, Any]]:
    """Lista identidades pareadas quando a credencial atual é admin."""
    payload = request_json(device, "/v1/clients")
    clients = payload.get("clients")
    if not isinstance(clients, list):
        raise MiraiRuntimeError(
            f"Agent '{device.name}' retornou clientes inválidos"
        )
    return clients


def set_agent_client_role(
    device: Device,
    client_id: str,
    role: str,
) -> dict[str, Any]:
    """Altera um papel remoto usando uma credencial administrativa."""
    return request_json(
        device,
        f"/v1/clients/{client_id}",
        method="PATCH",
        payload={"role": role},
    )


def activate_deployment(
    device: Device,
    deployment_id: str,
) -> dict[str, Any]:
    """Ativa um deployment validado no dispositivo."""
    return request_json(
        device,
        f"/v1/deployments/{deployment_id}/activate",
        method="POST",
    )


def deactivate_deployment(device: Device) -> dict[str, Any]:
    """Remove o deployment ativo durante um rollback transacional."""
    return request_json(
        device,
        "/v1/deployments/deactivate",
        method="POST",
    )


def run_remote_model(
    device: Device,
    input_specs: list[str] | None,
    layout: str,
    model_name: str | None = None,
) -> dict[str, Any]:
    """Executa uma inferência no deployment ativo do Agent."""
    encoded_inputs, attachments = encode_remote_inputs(input_specs)
    payload: dict[str, Any] = {
        "inputs": encoded_inputs,
        "layout": layout,
    }
    if attachments:
        payload["attachments"] = attachments
    if model_name is not None:
        payload["model"] = model_name
    return request_json(
        device,
        "/v1/inferences",
        method="POST",
        payload=payload,
    )


def deploy_model(
    device: Device,
    artifact_path: Path,
    provider_profile: str = "auto",
    signature_path: Path | None = None,
) -> dict[str, Any]:
    """Valida e envia um ONNX ou pacote .mirai usando um corpo binário."""
    provider_profile = normalize_provider_profile(provider_profile)
    validate_artifact(artifact_path)
    artifact_size = artifact_path.stat().st_size
    artifact_sha256 = calculate_sha256(artifact_path)
    is_package = artifact_path.suffix.lower() == MIRAI_EXTENSION
    connection: http.client.HTTPConnection | None = None
    headers = {
        "Accept": "application/json",
        "Content-Type": (
            MIRAI_MEDIA_TYPE if is_package else "application/octet-stream"
        ),
        "Content-Length": str(artifact_size),
        "X-Mirai-SHA256": artifact_sha256,
        "X-Mirai-Provider-Profile": provider_profile,
    }
    if is_package:
        headers["X-Mirai-Artifact-Name"] = artifact_path.name
    else:
        headers["X-Mirai-Model-Name"] = artifact_path.name
    if device.token:
        headers["Authorization"] = f"Bearer {device.token}"
    if signature_path is not None:
        try:
            signature = signature_path.expanduser().read_bytes()
        except OSError as error:
            raise MiraiRuntimeError(
                f"não foi possível ler a assinatura: {error}"
            ) from error
        if not signature:
            raise MiraiRuntimeError("arquivo de assinatura vazio")
        encoded_signature = base64.b64encode(signature).decode("ascii")
        if len(encoded_signature) > MAX_SIGNATURE_HEADER_CHARS:
            raise MiraiRuntimeError("arquivo de assinatura excede o limite do Agent")
        headers["X-Mirai-Signature"] = encoded_signature

    try:
        connection, prefix = _connection(device)
        with artifact_path.open("rb") as artifact_file:
            connection.request(
                "POST",
                f"{prefix}/v1/deployments",
                body=artifact_file,
                headers=headers,
            )
            return _decode_response(device, connection.getresponse())
    except (OSError, TimeoutError, http.client.HTTPException) as error:
        raise MiraiRuntimeError(
            f"falha ao enviar o artefato para o Agent '{device.name}'"
        ) from error
    finally:
        if connection is not None:
            connection.close()


def delete_deployment(device: Device, deployment_id: str) -> dict[str, Any]:
    """Remove um deployment inativo por identificador exato."""
    return request_json(
        device,
        f"/v1/deployments/{deployment_id}",
        method="DELETE",
    )


def deployment_retention_candidates(
    status: dict[str, Any],
    keep: int,
) -> list[dict[str, Any]]:
    """Seleciona deployments inativos antigos sem tocar no ativo."""
    if keep < 0:
        raise MiraiRuntimeError("keep não pode ser negativo")
    deployments = status.get("deployments")
    if not isinstance(deployments, list):
        raise MiraiRuntimeError("resposta de deployments inválida")
    active_id = status.get("active_deployment_id")
    inactive = [
        item
        for item in deployments
        if isinstance(item, dict) and item.get("deployment_id") != active_id
    ]
    inactive.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return inactive[keep:]
