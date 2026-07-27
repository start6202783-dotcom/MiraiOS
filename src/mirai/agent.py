"""Mirai Agent: destino mínimo para deploys locais do Projeto Hikari."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import tempfile
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import __version__
from .errors import MiraiRuntimeError
from .inspect import validate_model
from .runtime import create_session, load_runtime_dependencies


MAX_MODEL_SIZE_BYTES = 512 * 1024 * 1024
SAFE_MODEL_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class AgentState:
    """Mantém armazenamento e eventos de uma instância do Mirai Agent."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.expanduser().resolve()
        self.models_dir = self.data_dir / "models"
        self.events_path = self.data_dir / "events.jsonl"
        self._events_lock = threading.Lock()
        self.models_dir.mkdir(parents=True, exist_ok=True)

    def append_event(self, event: dict[str, Any]) -> None:
        """Acrescenta um evento JSONL de forma segura entre threads."""
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
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


class MiraiAgentHandler(BaseHTTPRequestHandler):
    """Implementa a API HTTP v1 do Agent sem dependências web externas."""

    server: MiraiAgentServer

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
        self.send_header("X-Mirai-Agent-Version", __version__)
        self.end_headers()
        self.wfile.write(encoded_payload)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": message})

    def do_GET(self) -> None:
        """Atende health check, informações e eventos."""
        request_url = urlsplit(self.path)
        if request_url.path == "/v1/health":
            self._send_json(
                HTTPStatus.OK,
                {"status": "ok", "agent_version": __version__},
            )
            return

        if request_url.path == "/v1/info":
            self._send_json(HTTPStatus.OK, collect_device_info())
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

        self._send_error(HTTPStatus.NOT_FOUND, "endpoint não encontrado")

    def do_POST(self) -> None:
        """Recebe um modelo ONNX e cria um deployment validado."""
        if urlsplit(self.path).path != "/v1/deployments":
            self._send_error(HTTPStatus.NOT_FOUND, "endpoint não encontrado")
            return

        try:
            payload = receive_deployment(self, self.server.state)
        except AgentRequestError as error:
            self._send_error(error.status, str(error))
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

        self._send_json(HTTPStatus.CREATED, payload)


class AgentRequestError(ValueError):
    """Erro HTTP controlado causado pelos dados enviados pelo cliente."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


def collect_device_info() -> dict[str, Any]:
    """Coleta capacidades portáveis sem assumir um fabricante específico."""
    try:
        ort, _ = load_runtime_dependencies()
        providers = ort.get_available_providers()
    except MiraiRuntimeError:
        providers = []
    return {
        "agent_version": __version__,
        "hostname": platform.node() or "unknown",
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "providers": providers,
    }


def _safe_model_name(raw_name: str | None) -> str:
    if not raw_name:
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "cabeçalho X-Mirai-Model-Name ausente",
        )
    safe_name = SAFE_MODEL_NAME_PATTERN.sub("-", Path(raw_name).name).strip("-")
    if not safe_name.lower().endswith(".onnx"):
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "o modelo enviado deve usar a extensão .onnx",
        )
    return safe_name[:128]


def _content_length(handler: MiraiAgentHandler) -> int:
    raw_length = handler.headers.get("Content-Length")
    try:
        content_length = int(raw_length or "")
    except ValueError as error:
        raise AgentRequestError(
            HTTPStatus.LENGTH_REQUIRED,
            "Content-Length ausente ou inválido",
        ) from error
    if content_length <= 0:
        raise AgentRequestError(HTTPStatus.BAD_REQUEST, "modelo vazio")
    if content_length > MAX_MODEL_SIZE_BYTES:
        raise AgentRequestError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "modelo excede o limite de 512 MB desta versão",
        )
    return content_length


def receive_deployment(
    handler: MiraiAgentHandler,
    state: AgentState,
) -> dict[str, Any]:
    """Recebe, verifica e ativa logicamente um modelo no dispositivo."""
    model_name = _safe_model_name(handler.headers.get("X-Mirai-Model-Name"))
    expected_sha256 = handler.headers.get("X-Mirai-SHA256", "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise AgentRequestError(
            HTTPStatus.BAD_REQUEST,
            "cabeçalho X-Mirai-SHA256 ausente ou inválido",
        )
    content_length = _content_length(handler)
    digest = hashlib.sha256()
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            dir=state.models_dir,
            prefix=".upload-",
            suffix=".onnx",
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
                "SHA-256 do modelo não confere",
            )

        validate_model(temporary_path)
        ort, _ = load_runtime_dependencies()
        session = create_session(temporary_path, ort)
        providers = session.get_providers()
        del session

        deployment_id = actual_sha256[:16]
        target_path = state.models_dir / f"{deployment_id}-{model_name}"
        os.replace(temporary_path, target_path)
        temporary_path = None

        event = {
            "type": "deployment",
            "status": "ready",
            "deployment_id": deployment_id,
            "model": model_name,
            "sha256": actual_sha256,
            "size_bytes": content_length,
            "providers": providers,
        }
        state.append_event(event)
        return event
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def create_agent_server(
    host: str,
    port: int,
    data_dir: Path,
) -> MiraiAgentServer:
    """Cria um servidor configurável, inclusive com porta efêmera em testes."""
    return MiraiAgentServer((host, port), AgentState(data_dir))


def run_agent(host: str, port: int, data_dir: Path) -> None:
    """Executa o Agent até receber interrupção do processo."""
    server = create_agent_server(host, port, data_dir)
    actual_host, actual_port = server.server_address[:2]
    print(
        f"[MiraiOS] Agent v{__version__} ouvindo em "
        f"http://{actual_host}:{actual_port}"
    )
    print(f"[MiraiOS] Dados do Agent: {server.state.data_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[MiraiOS] Encerrando Agent.")
    finally:
        server.server_close()
