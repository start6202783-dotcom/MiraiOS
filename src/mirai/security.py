"""Identidade, TLS, pareamento e autenticação do Hikari Link."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import ssl
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .errors import MiraiRuntimeError


SECURITY_REGISTRY_VERSION = 1
PAIRING_CODE_TTL_SECONDS = 10 * 60
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIRING_CODE_LENGTH = 12
CLIENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")


class PairingDenied(ValueError):
    """Indica que uma tentativa de pareamento não pode prosseguir."""


class AuthenticationDenied(ValueError):
    """Indica credenciais ausentes, inválidas ou revogadas."""


def utc_now() -> datetime:
    """Retorna o instante UTC atual com timezone explícito."""
    return datetime.now(timezone.utc)


def utc_iso(moment: datetime | None = None) -> str:
    """Serializa um instante UTC no formato ISO 8601."""
    return (moment or utc_now()).astimezone(timezone.utc).isoformat()


def normalize_fingerprint(value: str) -> str:
    """Normaliza um fingerprint SHA-256 com ou sem separadores."""
    normalized = re.sub(r"[^0-9A-Fa-f]", "", value).lower()
    if not FINGERPRINT_PATTERN.fullmatch(normalized):
        raise MiraiRuntimeError(
            "fingerprint TLS inválido; informe os 64 caracteres do SHA-256"
        )
    return normalized


def format_fingerprint(value: str) -> str:
    """Formata um fingerprint para conferência humana."""
    normalized = normalize_fingerprint(value)
    return ":".join(
        normalized[index : index + 2].upper()
        for index in range(0, len(normalized), 2)
    )


def normalize_pairing_code(value: str) -> str:
    """Normaliza um código de pareamento digitado pelo usuário."""
    normalized = re.sub(r"[\s-]", "", value).upper()
    if (
        len(normalized) != PAIRING_CODE_LENGTH
        or any(character not in PAIRING_ALPHABET for character in normalized)
    ):
        raise PairingDenied("código de pareamento inválido")
    return normalized


def format_pairing_code(value: str) -> str:
    """Agrupa o código de pareamento para facilitar a leitura."""
    normalized = normalize_pairing_code(value)
    return "-".join(
        normalized[index : index + 4]
        for index in range(0, len(normalized), 4)
    )


def is_loopback_host(host: str | None) -> bool:
    """Informa se o host representa somente a máquina local."""
    if not host:
        return False
    normalized = host.strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _write_bytes_atomic(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(
        temporary_path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        mode,
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
        os.replace(temporary_path, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _save_private_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = (
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    _write_bytes_atomic(path, encoded, 0o600)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MiraiRuntimeError(
            f"não foi possível ler {label}: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise MiraiRuntimeError(f"{label} possui formato incompatível")
    return payload


def _certificate_fingerprint(certificate_path: Path) -> str:
    try:
        pem = certificate_path.read_text(encoding="ascii")
        der = ssl.PEM_cert_to_DER_cert(pem)
    except (OSError, ValueError) as error:
        raise MiraiRuntimeError(
            f"certificado TLS do Agent é inválido: {error}"
        ) from error
    return hashlib.sha256(der).hexdigest()


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    """Identidade TLS persistente de um Mirai Agent."""

    agent_id: str
    fingerprint: str
    certificate_path: Path
    private_key_path: Path
    created_at: str


def _generate_identity(data_dir: Path) -> AgentIdentity:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    except ModuleNotFoundError as error:
        raise MiraiRuntimeError(
            "a dependência 'cryptography' é necessária para o Hikari Link"
        ) from error

    agent_id = uuid.uuid4().hex
    created_at = utc_now()
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    subject = x509.Name(
        [
            x509.NameAttribute(
                NameOID.COMMON_NAME,
                f"MiraiOS Agent {agent_id[:12]}",
            )
        ]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(created_at - timedelta(minutes=1))
        .not_valid_after(created_at + timedelta(days=3650))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(ipaddress.ip_address("::1")),
                ]
            ),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    certificate_path = data_dir / "agent-cert.pem"
    private_key_path = data_dir / "agent-key.pem"
    identity_path = data_dir / "identity.json"
    certificate_bytes = certificate.public_bytes(serialization.Encoding.PEM)
    private_key_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    _write_bytes_atomic(private_key_path, private_key_bytes, 0o600)
    _write_bytes_atomic(certificate_path, certificate_bytes, 0o644)
    fingerprint = _certificate_fingerprint(certificate_path)
    _save_private_json(
        identity_path,
        {
            "version": SECURITY_REGISTRY_VERSION,
            "agent_id": agent_id,
            "fingerprint": fingerprint,
            "created_at": utc_iso(created_at),
        },
    )
    return AgentIdentity(
        agent_id=agent_id,
        fingerprint=fingerprint,
        certificate_path=certificate_path,
        private_key_path=private_key_path,
        created_at=utc_iso(created_at),
    )


def load_or_create_identity(data_dir: Path) -> AgentIdentity:
    """Carrega uma identidade completa ou cria uma nova de forma atômica."""
    identity_path = data_dir / "identity.json"
    certificate_path = data_dir / "agent-cert.pem"
    private_key_path = data_dir / "agent-key.pem"
    existing = [
        identity_path.exists(),
        certificate_path.exists(),
        private_key_path.exists(),
    ]
    if any(existing) and not all(existing):
        raise MiraiRuntimeError(
            "identidade do Agent está incompleta; preserve ou remova "
            "identity.json, agent-cert.pem e agent-key.pem em conjunto"
        )
    if not any(existing):
        return _generate_identity(data_dir)

    payload = _load_json(identity_path, "a identidade do Agent")
    agent_id = payload.get("agent_id")
    fingerprint = payload.get("fingerprint")
    created_at = payload.get("created_at")
    if (
        payload.get("version") != SECURITY_REGISTRY_VERSION
        or not isinstance(agent_id, str)
        or not re.fullmatch(r"[0-9a-f]{32}", agent_id)
        or not isinstance(fingerprint, str)
        or not isinstance(created_at, str)
    ):
        raise MiraiRuntimeError("identidade do Agent possui formato incompatível")

    actual_fingerprint = _certificate_fingerprint(certificate_path)
    if not secrets.compare_digest(
        normalize_fingerprint(fingerprint),
        actual_fingerprint,
    ):
        raise MiraiRuntimeError(
            "fingerprint do certificado não corresponde à identidade do Agent"
        )
    try:
        os.chmod(private_key_path, 0o600)
        os.chmod(identity_path, 0o600)
    except OSError:
        pass
    return AgentIdentity(
        agent_id=agent_id,
        fingerprint=actual_fingerprint,
        certificate_path=certificate_path,
        private_key_path=private_key_path,
        created_at=created_at,
    )


class AgentSecurity:
    """Gerencia o canal seguro e os clientes pareados de um Agent."""

    def __init__(
        self,
        data_dir: Path,
        *,
        secure: bool,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.secure = secure
        self.clients_path = data_dir / "clients.json"
        self._clock = clock
        self._lock = threading.Lock()
        self.identity = load_or_create_identity(data_dir) if secure else None
        self._pairing_code: str | None = None
        self._pairing_code_hash: str | None = None
        self._pairing_expires_at: datetime | None = None
        if secure:
            self.rotate_pairing_code()

    @property
    def agent_id(self) -> str | None:
        return self.identity.agent_id if self.identity else None

    @property
    def fingerprint(self) -> str | None:
        return self.identity.fingerprint if self.identity else None

    @property
    def pairing_code(self) -> str | None:
        return (
            format_pairing_code(self._pairing_code)
            if self._pairing_code
            else None
        )

    @property
    def pairing_expires_at(self) -> datetime | None:
        return self._pairing_expires_at

    def pairing_available(self) -> bool:
        """Informa se existe um código ainda válido e não consumido."""
        return bool(
            self.secure
            and self._pairing_code_hash
            and self._pairing_expires_at
            and self._clock() < self._pairing_expires_at
        )

    def rotate_pairing_code(self) -> str:
        """Cria um segredo efêmero de uso único para um novo cliente."""
        raw_code = "".join(
            secrets.choice(PAIRING_ALPHABET)
            for _ in range(PAIRING_CODE_LENGTH)
        )
        with self._lock:
            self._pairing_code = raw_code
            self._pairing_code_hash = hashlib.sha256(
                raw_code.encode("ascii")
            ).hexdigest()
            self._pairing_expires_at = self._clock() + timedelta(
                seconds=PAIRING_CODE_TTL_SECONDS
            )
        return format_pairing_code(raw_code)

    def _empty_clients(self) -> dict[str, Any]:
        return {
            "version": SECURITY_REGISTRY_VERSION,
            "clients": [],
        }

    def _load_clients_unlocked(self) -> dict[str, Any]:
        if not self.clients_path.exists():
            return self._empty_clients()
        payload = _load_json(self.clients_path, "os clientes pareados")
        clients = payload.get("clients")
        if (
            payload.get("version") != SECURITY_REGISTRY_VERSION
            or not isinstance(clients, list)
        ):
            raise MiraiRuntimeError(
                "registro de clientes pareados possui formato incompatível"
            )
        for client in clients:
            if (
                not isinstance(client, dict)
                or not isinstance(client.get("client_id"), str)
                or not isinstance(client.get("name"), str)
                or not isinstance(client.get("token_sha256"), str)
            ):
                raise MiraiRuntimeError(
                    "registro de clientes pareados está corrompido"
                )
        return payload

    def _save_clients_unlocked(self, payload: dict[str, Any]) -> None:
        _save_private_json(self.clients_path, payload)

    @staticmethod
    def _public_client(client: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in client.items()
            if key != "token_sha256"
        }

    def pair(self, name: str, code: str) -> dict[str, Any]:
        """Consome o código efêmero e devolve o token apenas uma vez."""
        if not self.secure or self.identity is None:
            raise PairingDenied("pareamento exige um Agent com HTTPS")
        if not CLIENT_NAME_PATTERN.fullmatch(name):
            raise PairingDenied(
                "nome do cliente inválido; use letras, números, ponto, "
                "hífen ou sublinhado"
            )
        normalized_code = normalize_pairing_code(code)

        with self._lock:
            if (
                not self._pairing_code_hash
                or not self._pairing_expires_at
                or self._clock() >= self._pairing_expires_at
            ):
                raise PairingDenied(
                    "código de pareamento expirado ou já utilizado"
                )
            submitted_hash = hashlib.sha256(
                normalized_code.encode("ascii")
            ).hexdigest()
            if not secrets.compare_digest(
                submitted_hash,
                self._pairing_code_hash,
            ):
                raise PairingDenied("código de pareamento inválido")

            token = secrets.token_urlsafe(32)
            now = utc_iso(self._clock())
            client = {
                "client_id": secrets.token_hex(8),
                "name": name,
                "token_sha256": hashlib.sha256(
                    token.encode("ascii")
                ).hexdigest(),
                "created_at": now,
                "last_seen_at": now,
            }
            payload = self._load_clients_unlocked()
            payload["clients"].append(client)
            self._save_clients_unlocked(payload)
            self._pairing_code = None
            self._pairing_code_hash = None
            self._pairing_expires_at = None

        return {
            **self._public_client(client),
            "token": token,
            "agent_id": self.identity.agent_id,
            "fingerprint": self.identity.fingerprint,
        }

    def authenticate(self, token: str | None) -> dict[str, Any]:
        """Valida um bearer token e atualiza o último uso do cliente."""
        if not self.secure:
            return {
                "client_id": "local",
                "name": "local",
                "created_at": None,
                "last_seen_at": utc_iso(self._clock()),
            }
        if not token or not TOKEN_PATTERN.fullmatch(token):
            raise AuthenticationDenied("token de acesso ausente ou inválido")

        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._lock:
            payload = self._load_clients_unlocked()
            client = next(
                (
                    item
                    for item in payload["clients"]
                    if secrets.compare_digest(
                        item["token_sha256"],
                        token_hash,
                    )
                ),
                None,
            )
            if client is None:
                raise AuthenticationDenied(
                    "token de acesso inválido ou revogado"
                )
            client["last_seen_at"] = utc_iso(self._clock())
            self._save_clients_unlocked(payload)
            return self._public_client(client)

    def revoke(self, token: str | None) -> dict[str, Any]:
        """Revoga o próprio token autenticado."""
        client = self.authenticate(token)
        if not self.secure:
            raise AuthenticationDenied(
                "um dispositivo local sem pareamento não pode ser revogado"
            )
        with self._lock:
            payload = self._load_clients_unlocked()
            payload["clients"] = [
                item
                for item in payload["clients"]
                if item.get("client_id") != client["client_id"]
            ]
            self._save_clients_unlocked(payload)
        return client

    def list_clients(self) -> list[dict[str, Any]]:
        """Lista clientes sem expor hashes de autenticação."""
        with self._lock:
            payload = self._load_clients_unlocked()
            return [
                self._public_client(client)
                for client in payload["clients"]
            ]

    def create_server_context(self) -> ssl.SSLContext:
        """Cria um contexto TLS de servidor com identidade persistente."""
        if not self.secure or self.identity is None:
            raise MiraiRuntimeError("o Agent não possui uma identidade TLS")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        try:
            context.load_cert_chain(
                certfile=self.identity.certificate_path,
                keyfile=self.identity.private_key_path,
            )
        except (OSError, ssl.SSLError) as error:
            raise MiraiRuntimeError(
                f"não foi possível carregar a identidade TLS: {error}"
            ) from error
        return context
