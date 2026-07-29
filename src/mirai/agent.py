"""Mirai Agent: destino mínimo para deploys locais do Projeto Hikari."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import ssl
import sys
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .errors import MiraiRuntimeError
from .inputs import IMAGE_EXTENSIONS
from .inspect import validate_model
from .package import (
    MAX_MODEL_SIZE_BYTES,
    MAX_PACKAGE_SIZE_BYTES,
    MIRAI_EXTENSION,
    extract_mirai_model,
    load_mirai_package,
    validate_runtime_contract,
)
from .runtime import create_session, load_runtime_dependencies, run_model
from .security import (
    AgentSecurity,
    AuthenticationDenied,
    PairingDenied,
    format_fingerprint,
    is_loopback_host,
)


MAX_ARTIFACT_SIZE_BYTES = MAX_PACKAGE_SIZE_BYTES
MAX_JSON_BODY_BYTES = 1024 * 1024
DEPLOYMENT_REGISTRY_VERSION = 1
SAFE_MODEL_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
DEPLOYMENT_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")


class AgentRequestError(ValueError):
    """Erro HTTP controlado causado pelos dados enviados pelo cliente."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_deployment(deployment: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in deployment.items()
        if key not in {"file_name", "package_file_name"}
    }


class AgentState:
    """Mantém armazenamento e eventos de uma instância do Mirai Agent."""

    def __init__(
        self,
        data_dir: Path,
        *,
        secure: bool = False,
        security_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.data_dir = data_dir.expanduser().resolve()
        self.models_dir = self.data_dir / "models"
        self.packages_dir = self.data_dir / "packages"
        self.events_path = self.data_dir / "events.jsonl"
        self.deployments_path = self.data_dir / "deployments.json"
        self._events_lock = threading.Lock()
        self._deployments_lock = threading.Lock()
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        security_options: dict[str, Any] = {"secure": secure}
        if security_clock is not None:
            security_options["clock"] = security_clock
        self.security = AgentSecurity(
            self.data_dir,
            **security_options,
        )

    def append_event(self, event: dict[str, Any]) -> None:
        """Acrescenta um evento JSONL de forma segura entre threads."""
        payload = {
            "timestamp": _utc_now(),
            **event,
        }
        with self._events_lock:
            with self.events_path.open("a", encoding="utf-8") as event_file:
                event_file.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def recent_events(self, limit: int) -> list[dict[str, Any]]:
        """Retorna os eventos mais recentes, do mais novo para o mais antigo."""
        if not self.events_path.exists():
            return []
        with self._events_lock:
            try:
                lines = self.events_path.read_text(encoding="utf-8").splitlines()
                events = [json.loads(line) for line in lines[-limit:]]
            except (OSError, json.JSONDecodeError) as error:
                raise MiraiRuntimeError(
                    f"não foi possível ler os eventos do Agent: {error}"
                ) from error
        return list(reversed(events))

    def _empty_registry(self) -> dict[str, Any]:
        return {
            "version": DEPLOYMENT_REGISTRY_VERSION,
            "active_deployment_id": None,
            "deployments": [],
        }

    def _load_registry_unlocked(self) -> dict[str, Any]:
        if not self.deployments_path.exists():
            return self._empty_registry()
        try:
            registry = json.loads(
                self.deployments_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as error:
            raise MiraiRuntimeError(
                f"não foi possível ler os deployments do Agent: {error}"
            ) from error

        if (
            not isinstance(registry, dict)
            or registry.get("version") != DEPLOYMENT_REGISTRY_VERSION
            or not isinstance(registry.get("deployments"), list)
        ):
            raise MiraiRuntimeError(
                "registro de deployments possui formato incompatível"
            )
        return registry

    def _save_registry_unlocked(self, registry: dict[str, Any]) -> None:
        temporary_path = self.deployments_path.with_suffix(".tmp")
        try:
            temporary_path.write_text(
                json.dumps(registry, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_path, self.deployments_path)
        except OSError as error:
            raise MiraiRuntimeError(
                f"não foi possível salvar os deployments do Agent: {error}"
            ) from error

    def register_deployment(
        self,
        deployment: dict[str, Any],
        file_name: str,
    ) -> dict[str, Any]:
        """Registra um modelo pronto sem perder uma ativação existente."""
        with self._deployments_lock:
            registry = self._load_registry_unlocked()
            deployment_id = deployment["deployment_id"]
            existing = next(
                (
                    item
                    for item in registry["deployments"]
                    if item.get("deployment_id") == deployment_id
                ),
                None,
            )
            now = _utc_now()
            status = (
                "active"
                if registry.get("active_deployment_id") == deployment_id
                else "ready"
            )
            stored = {
                **deployment,
                "status": status,
                "file_name": file_name,
                "created_at": (
                    existing.get("created_at", now) if existing else now
                ),
                "updated_at": now,
            }
            if existing is None:
                registry["deployments"].append(stored)
            else:
                registry["deployments"] = [
                    stored
                    if item.get("deployment_id") == deployment_id
                    else item
                    for item in registry["deployments"]
                ]
            self._save_registry_unlocked(registry)
            return _public_deployment(stored)

    def deployment_status(self) -> dict[str, Any]:
        """Retorna o lifecycle atual de todos os modelos do Agent."""
        with self._deployments_lock:
            registry = self._load_registry_unlocked()
            deployments = [
                _public_deployment(item)
                for item in registry["deployments"]
            ]
            deployments.sort(
                key=lambda item: item.get("created_at", ""),
                reverse=True,
            )
            return {
                "active_deployment_id": registry.get("active_deployment_id"),
                "deployments": deployments,
            }

    def activate_deployment(self, deployment_id: str) -> dict[str, Any]:
        """Ativa um deployment e devolve o anterior ao estado ready."""
        if not DEPLOYMENT_ID_PATTERN.fullmatch(deployment_id):
            raise AgentRequestError(
                HTTPStatus.BAD_REQUEST,
                "identificador de deployment inválido",
            )

        with self._deployments_lock:
            registry = self._load_registry_unlocked()
            target = next(
                (
                    item
                    for item in registry["deployments"]
                    if item.get("deployment_id") == deployment_id
                ),
                None,
            )
            if target is None:
                raise AgentRequestError(
                    HTTPStatus.NOT_FOUND,
                    f"deployment '{deployment_id}' não encontrado",
                )

            now = _utc_now()
            for item in registry["deployments"]:
                if item.get("deployment_id") == deployment_id:
                    item["status"] = "active"
                    item["updated_at"] = now
                elif item.get("status") == "active":
                    item["status"] = "ready"
                    item["updated_at"] = now
            registry["active_deployment_id"] = deployment_id
            self._save_registry_unlocked(registry)
            return _public_deployment(target)

    def active_deployment(
        self,
        expected_model: str | None = None,
    ) -> tuple[dict[str, Any], Path]:
        """Resolve o deployment ativo e seu arquivo validado."""
        with self._deployments_lock:
            registry = self._load_registry_unlocked()
            active_id = registry.get("active_deployment_id")
            if not active_id:
                raise AgentRequestError(
                    HTTPStatus.CONFLICT,
                    "nenhum deployment está ativo",
                )
            deployment = next(
                (
                    item
                    for item in registry["deployments"]
                    if item.get("deployment_id") == active_id
                ),
                None,
            )
            if deployment is None:
                raise MiraiRuntimeError(
                    "o deployment ativo não existe no registro"
                )

            if expected_model:
                artifact_name = _safe_artifact_name(expected_model)
                accepted_names = {
                    deployment.get("model"),
                    deployment.get("artifact_name"),
                }
                if artifact_name not in accepted_names:
                    raise AgentRequestError(
                        HTTPStatus.CONFLICT,
                        f"o modelo ativo é '{deployment.get('model')}', "
                        f"não '{artifact_name}'",
                    )

            model_path = self.models_dir / deployment["file_name"]
            if not model_path.exists():
                raise MiraiRuntimeError(
                    f"arquivo do deployment '{active_id}' não foi encontrado"
                )
            return _public_deployment(deployment), model_path


class MiraiAgentServer(ThreadingHTTPServer):
    """Servidor HTTP com estado explícito do Agent."""

    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        state: AgentState,
    ) -> None:
        super().__init__(server_address, MiraiAgentHandler)
        self.state = state
        self.secure = state.security.secure
        if self.secure:
            context = state.security.create_server_context()
            self.socket = context.wrap_socket(
                self.socket,
                server_side=True,
            )

    def handle_error(
        self,
        request: object,
        client_address: tuple[str, int],
    ) -> None:
        """Silencia desconexões normais durante a verificação do TLS."""
        error = sys.exc_info()[1]
        if isinstance(
            error,
            (BrokenPipeError, ConnectionResetError, ssl.SSLError),
        ):
            return
        super().handle_error(request, client_address)


class MiraiAgentHandler(BaseHTTPRequestHandler):
    """Implementa a API HTTP v1 do Agent sem dependências web externas."""

    server: MiraiAgentServer
    authenticated_client: dict[str, Any] | None = None

    def log_message(self, format: str, *args: object) -> None:
        """Evita duplicar no stderr os eventos persistidos pelo Agent."""

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded_payload = json.dumps(
            payload,
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded_payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Mirai-Agent-Version", __version__)
        self.end_headers()
        self.wfile.write(encoded_payload)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": message})

    def _bearer_token(self) -> str | None:
        authorization = self.headers.get("Authorization", "")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer":
            return None
        return token.strip() or None

    def _authorize(self) -> bool:
        try:
            self.authenticated_client = (
                self.server.state.security.authenticate(
                    self._bearer_token()
                )
            )
        except AuthenticationDenied as error:
            self.send_response(HTTPStatus.UNAUTHORIZED)
            encoded_payload = json.dumps(
                {"error": str(error)},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8",
            )
            self.send_header("Content-Length", str(len(encoded_payload)))
            self.send_header("WWW-Authenticate", "Bearer")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Mirai-Agent-Version", __version__)
            self.end_headers()
            self.wfile.write(encoded_payload)
            return False
        except MiraiRuntimeError as error:
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                str(error),
            )
            return False
        return True

    def do_GET(self) -> None:
        """Atende health check, informações e eventos."""
        request_url = urlsplit(self.path)
        if request_url.path == "/v1/health":
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "agent_version": __version__,
                    "agent_id": self.server.state.security.agent_id,
                    "tls": self.server.secure,
                    "auth_required": self.server.secure,
                    "pairing_available": (
                        self.server.state.security.pairing_available()
                    ),
                },
            )
            return

        if not self._authorize():
            return

        if request_url.path == "/v1/info":
            self._send_json(
                HTTPStatus.OK,
                collect_device_info(self.server.state),
            )
            return

        if request_url.path == "/v1/deployments":
            try:
                status = self.server.state.deployment_status()
            except MiraiRuntimeError as error:
                self._send_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    str(error),
                )
                return
            self._send_json(HTTPStatus.OK, status)
            return

        if request_url.path == "/v1/logs":
            query = parse_qs(request_url.query)
            try:
                limit = int(query.get("limit", ["20"])[0])
            except ValueError:
                self._send_error(HTTPStatus.BAD_REQUEST, "limit deve ser inteiro")
                return
            if not 1 <= limit <= 200:
                self._send_error(
                    HTTPStatus.BAD_REQUEST,
                    "limit deve estar entre 1 e 200",
                )
                return
            try:
                events = self.server.state.recent_events(limit)
            except MiraiRuntimeError as error:
                self._send_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    str(error),
                )
                return
            self._send_json(HTTPStatus.OK, {"events": events})
            return

        if request_url.path == "/v1/clients":
            try:
                clients = self.server.state.security.list_clients()
            except MiraiRuntimeError as error:
                self._send_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    str(error),
                )
                return
            self._send_json(HTTPStatus.OK, {"clients": clients})
            return

        self._send_error(HTTPStatus.NOT_FOUND, "endpoint não encontrado")

    def do_POST(self) -> None:
        """Recebe modelos, ativa deployments e executa inferências."""
        request_path = urlsplit(self.path).path

        try:
            if request_path == "/v1/pair":
                payload = receive_pairing(self, self.server.state)
                status = HTTPStatus.CREATED
            elif not self._authorize():
                return
            elif request_path == "/v1/deployments":
                payload = receive_deployment(self, self.server.state)
                status = HTTPStatus.CREATED
            elif request_path == "/v1/inferences":
                payload = receive_inference(self, self.server.state)
                status = HTTPStatus.OK
            else:
                activate_match = re.fullmatch(
                    r"/v1/deployments/([0-9a-f]{16})/activate",
                    request_path,
                )
                if activate_match is None:
                    self._send_error(
                        HTTPStatus.NOT_FOUND,
                        "endpoint não encontrado",
                    )
                    return
                payload = activate_deployment(
                    self.server.state,
                    activate_match.group(1),
                )
                status = HTTPStatus.OK
        except AgentRequestError as error:
            self._send_error(error.status, str(error))
            return
        except PairingDenied as error:
            self._send_error(HTTPStatus.UNAUTHORIZED, str(error))
            return
        except MiraiRuntimeError as error:
            self._send_error(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
            return
        except OSError as error:
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"falha ao armazenar o modelo: {error}",
            )
            return

        self._send_json(status, payload)

    def do_DELETE(self) -> None:
        """Revoga o cliente autenticado quando solicitado."""
        request_path = urlsplit(self.path).path
        if request_path != "/v1/clients/self":
            self._send_error(HTTPStatus.NOT_FOUND, "endpoint não encontrado")
            return
        if not self._authorize():
            return
        try:
            client = self.server.state.security.revoke(
                self._bearer_token()
            )
            self.server.state.append_event(
                {
                    "type": "revocation",
                    "status": "success",
                    "client_id": client["client_id"],
                    "client": client["name"],
                }
            )
        except AuthenticationDenied as error:
            self._send_error(HTTPStatus.UNAUTHORIZED, str(error))
            return
        except MiraiRuntimeError as error:
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                str(error),
            )
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "status": "revoked",
                "client_id": client["client_id"],
            },
        )


def collect_device_info(state: AgentState | None = None) -> dict[str, Any]:
    """Coleta capacidades portáveis sem assumir um fabricante específico."""
    try:
        ort, _ = load_runtime_dependencies()
        providers = ort.get_available_providers()
    except MiraiRuntimeError:
        providers = []
    return {
        "agent_version": __version__,
        "agent_id": state.security.agent_id if state else None,
        "tls": state.security.secure if state else False,
        "hostname": platform.node() or "unknown",
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "memory_total_bytes": _memory_total_bytes(),
        "providers": providers,
    }


def _memory_total_bytes() -> int | None:
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        physical_pages = os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None
    if not isinstance(page_size, int) or not isinstance(physical_pages, int):
        return None
    return page_size * physical_pages


def _safe_artifact_name(raw_name: str | None) -> str:
    if not raw_name:
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "nome do artefato ausente",
        )
    safe_name = SAFE_MODEL_NAME_PATTERN.sub("-", Path(raw_name).name).strip("-")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".onnx", MIRAI_EXTENSION}:
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "o artefato enviado deve usar a extensão .onnx ou .mirai",
        )
    stem = safe_name[: -len(suffix)].strip(".-_")
    if not stem:
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "nome do artefato inválido",
        )
    return f"{stem[: 128 - len(suffix)]}{suffix}"


def _content_length(
    handler: MiraiAgentHandler,
    artifact_suffix: str,
) -> int:
    raw_length = handler.headers.get("Content-Length")
    try:
        content_length = int(raw_length or "")
    except ValueError as error:
        raise AgentRequestError(
            HTTPStatus.LENGTH_REQUIRED,
            "Content-Length ausente ou inválido",
        ) from error
    if content_length <= 0:
        raise AgentRequestError(HTTPStatus.BAD_REQUEST, "artefato vazio")
    limit = (
        MAX_ARTIFACT_SIZE_BYTES
        if artifact_suffix == MIRAI_EXTENSION
        else MAX_MODEL_SIZE_BYTES
    )
    if content_length > limit:
        raise AgentRequestError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "artefato excede o limite desta versão",
        )
    return content_length


def _json_body(handler: MiraiAgentHandler) -> dict[str, Any]:
    raw_length = handler.headers.get("Content-Length")
    try:
        content_length = int(raw_length or "")
    except ValueError as error:
        raise AgentRequestError(
            HTTPStatus.LENGTH_REQUIRED,
            "Content-Length ausente ou inválido",
        ) from error
    if content_length <= 0:
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "corpo JSON vazio",
        )
    if content_length > MAX_JSON_BODY_BYTES:
        raise AgentRequestError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "corpo JSON excede o limite de 1 MB",
        )

    raw_body = handler.rfile.read(content_length)
    if len(raw_body) != content_length:
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "corpo JSON interrompido antes do fim",
        )
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "corpo JSON inválido",
        ) from error
    if not isinstance(payload, dict):
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "o corpo da requisição deve ser um objeto JSON",
        )
    return payload


def _remote_input_specs(payload: dict[str, Any]) -> list[str] | None:
    input_specs = payload.get("inputs")
    if input_specs is None:
        return None
    if (
        not isinstance(input_specs, list)
        or not all(isinstance(item, str) for item in input_specs)
    ):
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "'inputs' deve ser uma lista de strings",
        )

    for spec in input_specs:
        _, separator, value = spec.partition("=")
        raw_value = value if separator else spec
        if Path(raw_value).suffix.lower() in IMAGE_EXTENSIONS:
            raise AgentRequestError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "imagens remotas ainda não são suportadas nesta versão; "
                "use entradas numéricas ou arrays JSON",
            )
    return input_specs


def receive_pairing(
    handler: MiraiAgentHandler,
    state: AgentState,
) -> dict[str, Any]:
    """Consome um código efêmero e registra um cliente autenticado."""
    payload = _json_body(handler)
    client_name = payload.get("name")
    pairing_code = payload.get("code")
    if not isinstance(client_name, str) or not isinstance(
        pairing_code,
        str,
    ):
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "'name' e 'code' devem ser strings",
        )
    pairing = state.security.pair(client_name, pairing_code)
    state.append_event(
        {
            "type": "pairing",
            "status": "success",
            "client_id": pairing["client_id"],
            "client": pairing["name"],
        }
    )
    return pairing


def receive_deployment(
    handler: MiraiAgentHandler,
    state: AgentState,
) -> dict[str, Any]:
    """Recebe e verifica um modelo ONNX ou pacote .mirai no dispositivo."""
    artifact_name = _safe_artifact_name(
        handler.headers.get("X-Mirai-Artifact-Name")
        or handler.headers.get("X-Mirai-Model-Name")
    )
    artifact_suffix = Path(artifact_name).suffix.lower()
    expected_sha256 = handler.headers.get("X-Mirai-SHA256", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "cabeçalho X-Mirai-SHA256 ausente ou inválido",
        )
    content_length = _content_length(handler, artifact_suffix)
    digest = hashlib.sha256()
    temporary_path: Path | None = None
    temporary_model_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            dir=state.data_dir,
            prefix=".upload-",
            suffix=artifact_suffix,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            remaining = content_length
            while remaining:
                chunk = handler.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise AgentRequestError(
                        HTTPStatus.BAD_REQUEST,
                        "upload interrompido antes do fim",
                    )
                temporary_file.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)

        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise AgentRequestError(
                HTTPStatus.BAD_REQUEST,
                "SHA-256 do artefato não confere",
            )

        package_file_name: str | None = None
        contract: dict[str, Any] | None = None
        package_metadata: dict[str, Any] | None = None
        if artifact_suffix == MIRAI_EXTENSION:
            package = load_mirai_package(temporary_path)
            manifest = package.manifest
            with tempfile.NamedTemporaryFile(
                dir=state.models_dir,
                prefix=".package-model-",
                suffix=".onnx",
                delete=False,
            ) as temporary_model:
                temporary_model_path = Path(temporary_model.name)
            extract_mirai_model(package, temporary_model_path)
            model_path = temporary_model_path
            model_name = package.model_name
            model_sha256 = str(manifest["model"]["sha256"])
            model_size_bytes = int(manifest["model"]["size_bytes"])
            contract = {
                "inputs": manifest["inputs"],
                "outputs": manifest["outputs"],
            }
            package_metadata = {
                "name": package.name,
                "version": package.version,
                "format": manifest["format"],
                "format_version": manifest["format_version"],
                "description": manifest.get("description"),
            }
        else:
            model_path = temporary_path
            model_name = artifact_name
            model_sha256 = actual_sha256
            model_size_bytes = content_length

        validate_model(model_path)
        ort, _ = load_runtime_dependencies()
        session = create_session(model_path, ort)
        if contract is not None:
            validate_runtime_contract(
                manifest,
                session.get_inputs(),
                session.get_outputs(),
            )
        providers = session.get_providers()
        del session

        deployment_id = actual_sha256[:16]
        target_path = state.models_dir / f"{deployment_id}-{model_name}"
        if artifact_suffix == MIRAI_EXTENSION:
            if temporary_model_path is None:
                raise MiraiRuntimeError(
                    "modelo temporário do pacote não foi preparado"
                )
            os.replace(temporary_model_path, target_path)
            temporary_model_path = None
            package_target = (
                state.packages_dir / f"{deployment_id}-{artifact_name}"
            )
            os.replace(temporary_path, package_target)
            temporary_path = None
            package_file_name = package_target.name
        else:
            os.replace(temporary_path, target_path)
            temporary_path = None

        deployment_data = {
            "status": "ready",
            "deployment_id": deployment_id,
            "model": model_name,
            "artifact_name": artifact_name,
            "artifact_type": (
                "mirai" if artifact_suffix == MIRAI_EXTENSION else "onnx"
            ),
            "sha256": actual_sha256,
            "size_bytes": content_length,
            "model_sha256": model_sha256,
            "model_size_bytes": model_size_bytes,
            "providers": providers,
        }
        if package_metadata is not None:
            deployment_data["package"] = package_metadata
        if contract is not None:
            deployment_data["contract"] = contract
        if package_file_name is not None:
            deployment_data["package_file_name"] = package_file_name
        deployment = state.register_deployment(
            deployment_data,
            target_path.name,
        )
        state.append_event({"type": "deployment", **deployment})
        return deployment
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        if temporary_model_path is not None:
            temporary_model_path.unlink(missing_ok=True)


def activate_deployment(
    state: AgentState,
    deployment_id: str,
) -> dict[str, Any]:
    """Ativa um modelo pronto e registra a transição de lifecycle."""
    deployment = state.activate_deployment(deployment_id)
    state.append_event({"type": "activation", **deployment})
    return deployment


def receive_inference(
    handler: MiraiAgentHandler,
    state: AgentState,
) -> dict[str, Any]:
    """Executa o deployment ativo e devolve resultado e latência."""
    payload = _json_body(handler)
    input_specs = _remote_input_specs(payload)
    layout = payload.get("layout", "auto")
    if layout not in {"auto", "nchw", "nhwc"}:
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "layout deve ser auto, nchw ou nhwc",
        )
    expected_model = payload.get("model")
    if expected_model is not None and not isinstance(expected_model, str):
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "'model' deve ser uma string",
        )

    deployment, model_path = state.active_deployment(expected_model)
    contract = deployment.get("contract")
    preprocessing = None
    if isinstance(contract, dict):
        inputs = contract.get("inputs")
        if isinstance(inputs, list):
            preprocessing = {
                str(item["name"]): dict(item["preprocessing"])
                for item in inputs
                if isinstance(item, dict)
                and "name" in item
                and isinstance(item.get("preprocessing"), dict)
            }
    started_at = perf_counter()
    try:
        result, latency_ms = run_model(
            model_path,
            input_specs,
            layout,
            preprocessing,
        )
    except MiraiRuntimeError as error:
        state.append_event(
            {
                "type": "inference",
                "status": "failed",
                "deployment_id": deployment["deployment_id"],
                "model": deployment["model"],
                "error": str(error),
            }
        )
        raise
    total_ms = (perf_counter() - started_at) * 1000
    event = {
        "type": "inference",
        "status": "success",
        "deployment_id": deployment["deployment_id"],
        "model": deployment["model"],
        "latency_ms": latency_ms,
        "total_ms": total_ms,
    }
    state.append_event(event)
    return {
        **event,
        "result": result,
    }


def create_agent_server(
    host: str,
    port: int,
    data_dir: Path,
    *,
    secure: bool = False,
    security_clock: Callable[[], datetime] | None = None,
) -> MiraiAgentServer:
    """Cria um servidor configurável, inclusive com porta efêmera em testes."""
    return MiraiAgentServer(
        (host, port),
        AgentState(
            data_dir,
            secure=secure,
            security_clock=security_clock,
        ),
    )


def run_agent(
    host: str,
    port: int,
    data_dir: Path,
    *,
    force_secure: bool = False,
) -> None:
    """Executa o Agent até receber interrupção do processo."""
    secure = force_secure or not is_loopback_host(host)
    server = create_agent_server(
        host,
        port,
        data_dir,
        secure=secure,
    )
    actual_host, actual_port = server.server_address[:2]
    scheme = "https" if secure else "http"
    print(
        f"[MiraiOS] Agent v{__version__} ouvindo em "
        f"{scheme}://{actual_host}:{actual_port}"
    )
    print(f"[MiraiOS] Dados do Agent: {server.state.data_dir}")
    if secure:
        security = server.state.security
        print(f"[MiraiOS] Agent ID: {security.agent_id}")
        assert security.fingerprint is not None
        print(
            "[MiraiOS] Fingerprint TLS SHA-256: "
            f"{format_fingerprint(security.fingerprint)}"
        )
        print(
            "[MiraiOS] Código de pareamento (uso único): "
            f"{security.pairing_code}"
        )
        expires_at = security.pairing_expires_at
        if expires_at is not None:
            print(
                "[MiraiOS] Código válido até: "
                f"{expires_at.isoformat()}"
            )
    else:
        print(
            "[MiraiOS] Modo local sem autenticação; somente localhost."
        )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[MiraiOS] Encerrando Agent.")
    finally:
        server.server_close()
