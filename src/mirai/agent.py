"""Mirai Agent: destino mínimo para deploys locais do Projeto Hikari."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import socket
import ssl
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .admission import AdmissionPolicy, admit_artifact
from .attachments import materialize_remote_inputs
from .audit import AuditLog
from .discovery import AgentAdvertiser
from .errors import MiraiRuntimeError
from .inspect import validate_model_with_report
from .json_codec import strict_json_dumps, strict_json_loads
from .package import (
    MAX_MODEL_SIZE_BYTES,
    MAX_PACKAGE_SIZE_BYTES,
    MIRAI_EXTENSION,
    extract_mirai_model,
    load_mirai_package,
    validate_runtime_contract,
)
from .providers import hardware_profile, normalize_provider_profile
from .runtime import create_session, load_runtime_dependencies, run_model
from .security import (
    ACCESS_ROLES,
    AgentSecurity,
    AuthenticationDenied,
    PairingDenied,
    PairingRateLimited,
    format_fingerprint,
    is_loopback_host,
    role_allows,
)
from .storage import atomic_write_text, verify_file

MAX_ARTIFACT_SIZE_BYTES = MAX_PACKAGE_SIZE_BYTES
MAX_JSON_BODY_BYTES = 14 * 1024 * 1024
MAX_CONCURRENT_REQUESTS = 32
MAX_PARALLEL_WORK = 2
REQUEST_SOCKET_TIMEOUT_SECONDS = 15.0
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
        pairing_role: str = "admin",
        admission_policy: AdmissionPolicy | None = None,
        max_parallel_work: int = MAX_PARALLEL_WORK,
    ) -> None:
        self.data_dir = data_dir.expanduser().resolve()
        self.models_dir = self.data_dir / "models"
        self.packages_dir = self.data_dir / "packages"
        self.legacy_events_path = self.data_dir / "events.jsonl"
        self.audit = AuditLog(self.data_dir / "audit.jsonl")
        self.deployments_path = self.data_dir / "deployments.json"
        self._events_lock = threading.Lock()
        self._deployments_lock = threading.Lock()
        self._integrity_cache: dict[Path, tuple[int, int, int, int, int]] = {}
        if not 1 <= max_parallel_work <= MAX_CONCURRENT_REQUESTS:
            raise MiraiRuntimeError(
                "max_parallel_work deve estar entre 1 e "
                f"{MAX_CONCURRENT_REQUESTS}"
            )
        self._work_slots = threading.BoundedSemaphore(max_parallel_work)
        self.max_parallel_work = max_parallel_work
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        security_options: dict[str, Any] = {
            "secure": secure,
            "pairing_role": pairing_role,
        }
        if security_clock is not None:
            security_options["clock"] = security_clock
        self.security = AgentSecurity(
            self.data_dir,
            **security_options,
        )
        self.admission_policy = admission_policy or AdmissionPolicy()

    @contextmanager
    def work_slot(self) -> Iterator[None]:
        """Limita validações e inferências que consomem CPU/memória."""
        if not self._work_slots.acquire(blocking=False):
            raise AgentRequestError(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "o Agent atingiu o limite de operações pesadas simultâneas",
            )
        try:
            yield
        finally:
            self._work_slots.release()

    def append_event(self, event: dict[str, Any]) -> None:
        """Acrescenta um evento JSONL de forma segura entre threads."""
        payload = {
            "timestamp": _utc_now(),
            **event,
        }
        with self._events_lock:
            self.audit.append(payload)

    def recent_events(self, limit: int) -> list[dict[str, Any]]:
        """Retorna os eventos mais recentes, do mais novo para o mais antigo."""
        with self._events_lock:
            chained = self.audit.recent(limit)
            if not self.legacy_events_path.exists():
                return chained
            try:
                lines = self.legacy_events_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                events = [
                    strict_json_loads(line, label="evento do Agent")
                    for line in lines[-limit:]
                ]
            except (OSError, MiraiRuntimeError) as error:
                raise MiraiRuntimeError(
                    f"não foi possível ler os eventos do Agent: {error}"
                ) from error
        combined = [*chained, *reversed(events)]
        combined.sort(
            key=lambda item: str(item.get("timestamp", "")),
            reverse=True,
        )
        return combined[:limit]

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
            registry = strict_json_loads(
                self.deployments_path.read_bytes(),
                label="registro de deployments",
            )
        except (OSError, MiraiRuntimeError) as error:
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
        try:
            atomic_write_text(
                self.deployments_path,
                strict_json_dumps(registry, pretty=True) + "\n",
                mode=0o600,
            )
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
            model_path = self._stored_path(
                self.models_dir,
                target.get("file_name"),
            )
            if model_path is None or not model_path.is_file():
                raise MiraiRuntimeError(
                    f"arquivo do deployment '{deployment_id}' não foi encontrado"
                )
            self._verify_deployment_model_unlocked(target, model_path)

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

    def deactivate_deployment(self) -> dict[str, Any] | None:
        """Remove a seleção ativa e devolve o deployment anterior a ready."""
        with self._deployments_lock:
            registry = self._load_registry_unlocked()
            active_id = registry.get("active_deployment_id")
            if not active_id:
                return None

            now = _utc_now()
            previous: dict[str, Any] | None = None
            for item in registry["deployments"]:
                if item.get("deployment_id") == active_id:
                    item["status"] = "ready"
                    item["updated_at"] = now
                    previous = item
                    break
            registry["active_deployment_id"] = None
            self._save_registry_unlocked(registry)
            return _public_deployment(previous) if previous is not None else None

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
            self._verify_deployment_model_unlocked(deployment, model_path)
            return _public_deployment(deployment), model_path

    def _verify_deployment_model_unlocked(
        self,
        deployment: dict[str, Any],
        model_path: Path,
    ) -> None:
        try:
            stat_result = model_path.stat()
        except OSError as error:
            raise MiraiRuntimeError(
                f"não foi possível inspecionar o modelo: {error}"
            ) from error
        fingerprint = (
            stat_result.st_dev,
            stat_result.st_ino,
            stat_result.st_size,
            stat_result.st_mtime_ns,
            stat_result.st_ctime_ns,
        )
        if self._integrity_cache.get(model_path) == fingerprint:
            return
        verify_file(
            model_path,
            expected_sha256=str(
                deployment.get("model_sha256", deployment.get("sha256", ""))
            ),
            expected_size=int(
                deployment.get("model_size_bytes", deployment.get("size_bytes", -1))
            ),
            label=f"modelo do deployment '{deployment['deployment_id']}'",
        )
        self._integrity_cache[model_path] = fingerprint

    @staticmethod
    def _stored_path(directory: Path, file_name: Any) -> Path | None:
        if file_name is None:
            return None
        if not isinstance(file_name, str) or Path(file_name).name != file_name:
            raise MiraiRuntimeError("deployment contém um nome de arquivo inseguro")
        candidate = (directory / file_name).resolve()
        if candidate.parent != directory.resolve():
            raise MiraiRuntimeError("deployment aponta para fora do armazenamento")
        return candidate

    def delete_deployment(self, deployment_id: str) -> dict[str, Any]:
        """Exclui um deployment inativo com rollback da operação de arquivos."""
        if not DEPLOYMENT_ID_PATTERN.fullmatch(deployment_id):
            raise AgentRequestError(
                HTTPStatus.BAD_REQUEST,
                "identificador de deployment inválido",
            )
        with self._deployments_lock:
            registry = self._load_registry_unlocked()
            if registry.get("active_deployment_id") == deployment_id:
                raise AgentRequestError(
                    HTTPStatus.CONFLICT,
                    "o deployment ativo não pode ser removido",
                )
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
            paths = [
                path
                for path in (
                    self._stored_path(self.models_dir, target.get("file_name")),
                    self._stored_path(
                        self.packages_dir,
                        target.get("package_file_name"),
                    ),
                )
                if path is not None and path.exists()
            ]
            moved: list[tuple[Path, Path]] = []
            try:
                for path in paths:
                    tombstone = path.with_name(
                        f".{path.name}.delete-{uuid.uuid4().hex}"
                    )
                    os.replace(path, tombstone)
                    moved.append((path, tombstone))
                registry["deployments"] = [
                    item
                    for item in registry["deployments"]
                    if item.get("deployment_id") != deployment_id
                ]
                self._save_registry_unlocked(registry)
            except BaseException:
                for original, tombstone in reversed(moved):
                    if tombstone.exists():
                        os.replace(tombstone, original)
                raise
            for _, tombstone in moved:
                try:
                    tombstone.unlink(missing_ok=True)
                except OSError as error:
                    raise MiraiRuntimeError(
                        f"deployment removido, mas o tombstone não pôde ser apagado: {error}"
                    ) from error
            for original, _ in moved:
                self._integrity_cache.pop(original, None)
            return _public_deployment(target)


class MiraiAgentServer(ThreadingHTTPServer):
    """Servidor HTTP com estado explícito do Agent."""

    daemon_threads = True
    request_queue_size = 64

    def __init__(
        self,
        server_address: tuple[str, int],
        state: AgentState,
    ) -> None:
        super().__init__(server_address, MiraiAgentHandler)
        self.state = state
        self._request_slots = threading.BoundedSemaphore(
            MAX_CONCURRENT_REQUESTS
        )
        self.secure = state.security.secure
        if self.secure:
            context = state.security.create_server_context()
            self.socket = context.wrap_socket(
                self.socket,
                server_side=True,
            )

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        request, client_address = super().get_request()
        request.settimeout(REQUEST_SOCKET_TIMEOUT_SECONDS)
        return request, client_address

    def process_request(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        request_socket = cast(socket.socket, request)
        if not self._request_slots.acquire(blocking=False):
            try:
                request_socket.sendall(
                    b"HTTP/1.0 503 Service Unavailable\r\n"
                    b"Connection: close\r\nContent-Length: 0\r\n\r\n"
                )
            finally:
                self.shutdown_request(request_socket)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
        client_address: tuple[str, int],
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    def handle_error(
        self,
        request: socket.socket | tuple[bytes, socket.socket],
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
    server_version = "MiraiOS"
    sys_version = ""

    def version_string(self) -> str:
        return "MiraiOS"

    def log_message(self, format: str, *args: object) -> None:
        """Evita duplicar no stderr os eventos persistidos pelo Agent."""

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        try:
            encoded_payload = strict_json_dumps(payload).encode("utf-8")
        except MiraiRuntimeError:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            encoded_payload = (
                b'{"error":"resposta do Agent nao pode ser serializada"}'
            )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded_payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
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

    def _authorize(self, required_role: str = "viewer") -> bool:
        try:
            self.authenticated_client = (
                self.server.state.security.authenticate(
                    self._bearer_token()
                )
            )
        except AuthenticationDenied as error:
            self.send_response(HTTPStatus.UNAUTHORIZED)
            encoded_payload = strict_json_dumps(
                {"error": str(error)}
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
        if self.authenticated_client is None:
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "autenticação concluída sem identidade do cliente",
            )
            return False
        if not role_allows(
            str(self.authenticated_client.get("role", "viewer")),
            required_role,
        ):
            self._send_error(
                HTTPStatus.FORBIDDEN,
                f"a operação exige o papel '{required_role}'",
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
                    "limits": {
                        "parallel_work": self.server.state.max_parallel_work,
                        "requests": MAX_CONCURRENT_REQUESTS,
                        "socket_timeout_seconds": REQUEST_SOCKET_TIMEOUT_SECONDS,
                    },
                },
            )
            return

        required_role = (
            "admin" if request_url.path == "/v1/clients" else "viewer"
        )
        if not self._authorize(required_role):
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

        if request_url.path == "/v1/audit":
            try:
                audit = self.server.state.audit.verify()
                legacy_records = (
                    len(
                        self.server.state.legacy_events_path.read_text(
                            encoding="utf-8"
                        ).splitlines()
                    )
                    if self.server.state.legacy_events_path.exists()
                    else 0
                )
            except (OSError, MiraiRuntimeError) as error:
                self._send_error(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
                return
            self._send_json(
                HTTPStatus.OK,
                {**audit, "legacy_records": legacy_records},
            )
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
            elif not self._authorize("operator"):
                return
            elif request_path == "/v1/deployments":
                with self.server.state.work_slot():
                    payload = receive_deployment(self, self.server.state)
                status = HTTPStatus.CREATED
            elif request_path == "/v1/inferences":
                with self.server.state.work_slot():
                    payload = receive_inference(self, self.server.state)
                status = HTTPStatus.OK
            elif request_path == "/v1/deployments/deactivate":
                payload = deactivate_deployment(self.server.state)
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
        except PairingRateLimited as error:
            self._send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": str(error), "retry_after": error.retry_after},
            )
            return
        except PairingDenied as error:
            self._send_error(HTTPStatus.UNAUTHORIZED, str(error))
            return
        except MiraiRuntimeError as error:
            self._send_error(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
            return
        except TimeoutError:
            self._send_error(
                HTTPStatus.REQUEST_TIMEOUT,
                "requisição excedeu o tempo limite",
            )
            return
        except OSError as error:
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                f"falha ao armazenar o modelo: {error}",
            )
            return

        self._send_json(status, payload)

    def do_PUT(self) -> None:
        self._send_error(HTTPStatus.METHOD_NOT_ALLOWED, "método não permitido")

    def do_OPTIONS(self) -> None:
        self._send_error(HTTPStatus.METHOD_NOT_ALLOWED, "método não permitido")

    def do_PATCH(self) -> None:
        """Altera papéis de acesso com uma credencial administrativa."""
        request_path = urlsplit(self.path).path
        match = re.fullmatch(r"/v1/clients/([0-9a-f]{16})", request_path)
        if match is None:
            self._send_error(HTTPStatus.NOT_FOUND, "endpoint não encontrado")
            return
        if not self._authorize("admin"):
            return
        try:
            payload = _json_body(self)
            if set(payload) != {"role"} or payload.get("role") not in ACCESS_ROLES:
                raise AgentRequestError(
                    HTTPStatus.BAD_REQUEST,
                    f"'role' deve ser {', '.join(ACCESS_ROLES)}",
                )
            client = self.server.state.security.set_client_role(
                match.group(1),
                str(payload["role"]),
            )
            actor = self.authenticated_client
            if actor is None:
                raise AgentRequestError(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "operação autenticada sem identidade do cliente",
                )
            self.server.state.append_event(
                {
                    "type": "role_change",
                    "status": "success",
                    "client_id": client["client_id"],
                    "role": client["role"],
                    "actor_client_id": actor["client_id"],
                }
            )
        except AgentRequestError as error:
            self._send_error(error.status, str(error))
            return
        except MiraiRuntimeError as error:
            self._send_error(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
            return
        self._send_json(HTTPStatus.OK, client)

    def do_DELETE(self) -> None:
        """Revoga clientes ou remove deployments inativos."""
        request_path = urlsplit(self.path).path
        deployment_match = re.fullmatch(
            r"/v1/deployments/([0-9a-f]{16})",
            request_path,
        )
        if request_path != "/v1/clients/self" and deployment_match is None:
            self._send_error(HTTPStatus.NOT_FOUND, "endpoint não encontrado")
            return
        required_role = "operator" if deployment_match is not None else "viewer"
        if not self._authorize(required_role):
            return
        if deployment_match is not None:
            try:
                deployment = self.server.state.delete_deployment(
                    deployment_match.group(1)
                )
                self.server.state.append_event(
                    {"type": "deployment_deleted", **deployment}
                )
            except AgentRequestError as error:
                self._send_error(error.status, str(error))
                return
            except MiraiRuntimeError as error:
                self._send_error(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
                return
            self._send_json(
                HTTPStatus.OK,
                {
                    "status": "deleted",
                    "deployment_id": deployment["deployment_id"],
                },
            )
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
    machine = platform.machine()
    system = platform.system()
    return {
        "agent_version": __version__,
        "agent_id": state.security.agent_id if state else None,
        "tls": state.security.secure if state else False,
        "hostname": platform.node() or "unknown",
        "system": system,
        "release": platform.release(),
        "machine": machine,
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "memory_total_bytes": _memory_total_bytes(),
        "providers": providers,
        "provider_profiles": ["auto", "cpu", "cuda", "directml"],
        "hardware_profile": hardware_profile(machine, system, providers),
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
            "corpo JSON excede o limite de 14 MB",
        )

    raw_body = handler.rfile.read(content_length)
    if len(raw_body) != content_length:
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "corpo JSON interrompido antes do fim",
        )
    try:
        payload = strict_json_loads(raw_body, label="corpo JSON")
    except MiraiRuntimeError as error:
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            str(error),
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

    return input_specs


def _install_verified_file(
    source: Path,
    target: Path,
    *,
    expected_sha256: str,
    expected_size: int,
) -> bool:
    """Instala por conteúdo; um alvo idêntico é reutilizado sem sobrescrita."""
    if target.exists():
        verify_file(
            target,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label=f"arquivo armazenado '{target.name}'",
        )
        source.unlink(missing_ok=True)
        return False
    try:
        os.chmod(source, 0o444)
    except OSError:
        pass
    try:
        os.link(source, target, follow_symlinks=False)
    except FileExistsError:
        verify_file(
            target,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label=f"arquivo armazenado '{target.name}'",
        )
        source.unlink(missing_ok=True)
        return False
    except OSError as error:
        raise MiraiRuntimeError(
            f"não foi possível instalar '{target.name}' sem sobrescrita: {error}"
        ) from error
    source.unlink(missing_ok=True)
    try:
        verify_file(
            target,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            label=f"arquivo armazenado '{target.name}'",
        )
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return True


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
    pairing = state.security.pair(
        client_name,
        pairing_code,
        peer_id=str(handler.client_address[0]),
    )
    state.append_event(
        {
            "type": "pairing",
            "status": "success",
            "client_id": pairing["client_id"],
            "client": pairing["name"],
            "role": pairing["role"],
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
    try:
        provider_profile = normalize_provider_profile(
            handler.headers.get("X-Mirai-Provider-Profile", "auto")
        )
    except MiraiRuntimeError as error:
        raise AgentRequestError(HTTPStatus.BAD_REQUEST, str(error)) from error
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

        admission = admit_artifact(
            temporary_path,
            handler.headers.get("X-Mirai-Signature"),
            state.admission_policy,
            artifact_name=artifact_name,
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

        _, model_safety = validate_model_with_report(model_path)
        ort, _ = load_runtime_dependencies()
        session = create_session(model_path, ort, provider_profile)
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
        installed: list[Path] = []
        try:
            if artifact_suffix == MIRAI_EXTENSION:
                if temporary_model_path is None:
                    raise MiraiRuntimeError(
                        "modelo temporário do pacote não foi preparado"
                    )
                if _install_verified_file(
                    temporary_model_path,
                    target_path,
                    expected_sha256=model_sha256,
                    expected_size=model_size_bytes,
                ):
                    installed.append(target_path)
                temporary_model_path = None
                package_target = (
                    state.packages_dir / f"{deployment_id}-{artifact_name}"
                )
                if _install_verified_file(
                    temporary_path,
                    package_target,
                    expected_sha256=actual_sha256,
                    expected_size=content_length,
                ):
                    installed.append(package_target)
                temporary_path = None
                package_file_name = package_target.name
            else:
                if _install_verified_file(
                    temporary_path,
                    target_path,
                    expected_sha256=model_sha256,
                    expected_size=model_size_bytes,
                ):
                    installed.append(target_path)
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
                "provider_profile": provider_profile,
                "admission": admission,
                "model_safety": model_safety.as_dict(),
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
        except BaseException:
            for installed_path in reversed(installed):
                installed_path.unlink(missing_ok=True)
            raise
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


def deactivate_deployment(state: AgentState) -> dict[str, Any]:
    """Desativa o deployment atual para concluir um rollback sem antecessor."""
    deployment = state.deactivate_deployment()
    event: dict[str, Any] = {
        "type": "deactivation",
        "status": "success",
    }
    if deployment is not None:
        event.update(
            {
                "deployment_id": deployment["deployment_id"],
                "model": deployment["model"],
            }
        )
    state.append_event(event)
    return {
        **event,
        "previous_deployment_id": (
            deployment["deployment_id"] if deployment is not None else None
        ),
    }


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
        with materialize_remote_inputs(
            input_specs,
            payload.get("attachments"),
        ) as materialized_specs:
            result, latency_ms = run_model(
                model_path,
                materialized_specs,
                layout,
                preprocessing,
                str(deployment.get("provider_profile", "auto")),
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
    pairing_role: str = "admin",
    admission_policy: AdmissionPolicy | None = None,
    max_parallel_work: int = MAX_PARALLEL_WORK,
) -> MiraiAgentServer:
    """Cria um servidor configurável, inclusive com porta efêmera em testes."""
    return MiraiAgentServer(
        (host, port),
        AgentState(
            data_dir,
            secure=secure,
            security_clock=security_clock,
            pairing_role=pairing_role,
            admission_policy=admission_policy,
            max_parallel_work=max_parallel_work,
        ),
    )


def run_agent(
    host: str,
    port: int,
    data_dir: Path,
    *,
    force_secure: bool = False,
    pairing_role: str = "admin",
    discoverable: bool = False,
    admission_mode: str = "open",
    trusted_keys: tuple[Path, ...] = (),
) -> None:
    """Executa o Agent até receber interrupção do processo."""
    secure = force_secure or not is_loopback_host(host)
    server = create_agent_server(
        host,
        port,
        data_dir,
        secure=secure,
        pairing_role=pairing_role,
        admission_policy=AdmissionPolicy(admission_mode, trusted_keys),
    )
    actual_host, actual_port = server.server_address[:2]
    if not isinstance(actual_host, str) or not isinstance(actual_port, int):
        raise MiraiRuntimeError("endereço do Agent possui formato inesperado")
    scheme = "https" if secure else "http"
    print(
        f"[MiraiOS] Agent v{__version__} ouvindo em "
        f"{scheme}://{actual_host}:{actual_port}"
    )
    print(f"[MiraiOS] Dados do Agent: {server.state.data_dir}")
    if secure:
        security = server.state.security
        print(f"[MiraiOS] Agent ID: {security.agent_id}")
        fingerprint = security.fingerprint
        if fingerprint is None:
            raise MiraiRuntimeError("identidade TLS ativa sem fingerprint")
        print(
            "[MiraiOS] Fingerprint TLS SHA-256: "
            f"{format_fingerprint(fingerprint)}"
        )
        print(
            "[MiraiOS] Código de pareamento (uso único): "
            f"{security.pairing_code}"
        )
        print(f"[MiraiOS] Papel concedido neste pareamento: {pairing_role}")
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
    print(f"[MiraiOS] Política de admissão: {admission_mode}")
    if trusted_keys:
        print(f"[MiraiOS] Chaves confiáveis: {len(trusted_keys)}")
    advertiser: AgentAdvertiser | None = None
    if discoverable:
        try:
            # O valor somente substitui o endereço do anúncio mDNS; não abre socket.
            advertised_host = (
                socket.gethostbyname(socket.gethostname())
                if host in {"0.0.0.0", "::"}  # nosec B104
                else host
            )
            advertiser = AgentAdvertiser(
                advertised_host,
                actual_port,
                agent_id=server.state.security.agent_id,
                tls=secure,
                version=__version__,
            )
            print("[MiraiOS] Descoberta mDNS ativa (candidatos não confiáveis).")
        except (OSError, MiraiRuntimeError) as error:
            server.server_close()
            raise MiraiRuntimeError(
                f"não foi possível iniciar descoberta mDNS: {error}"
            ) from error
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[MiraiOS] Encerrando Agent.")
    finally:
        if advertiser is not None:
            advertiser.close()
        server.server_close()
