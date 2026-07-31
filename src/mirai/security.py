"""Identidade, TLS, pareamento e autenticação do Hikari Link."""

from __future__ import annotations

import hashlib
import ipaddress
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
from .json_codec import strict_json_dumps, strict_json_loads
from .storage import atomic_write_bytes

SECURITY_REGISTRY_VERSION = 2
PAIRING_CODE_TTL_SECONDS = 10 * 60
PAIRING_ATTEMPT_WINDOW_SECONDS = 60
PAIRING_BLOCK_SECONDS = 5 * 60
PAIRING_MAX_FAILURES = 5
PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PAIRING_CODE_LENGTH = 12
CLIENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
CLIENT_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
ACCESS_ROLES = ("viewer", "operator", "admin")
ROLE_RANK = {role: index for index, role in enumerate(ACCESS_ROLES)}


class PairingDenied(ValueError):
    """Indica que uma tentativa de pareamento não pode prosseguir."""


class PairingRateLimited(PairingDenied):
    """Indica bloqueio temporário após tentativas repetidas."""

    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AuthenticationDenied(ValueError):
    """Indica credenciais ausentes, inválidas ou revogadas."""


def utc_now() -> datetime:
    """Retorna o instante UTC atual com timezone explícito."""
    return datetime.now(timezone.utc)


def utc_iso(moment: datetime | None = None) -> str:
    """Serializa um instante UTC no formato ISO 8601."""
    return (moment or utc_now()).astimezone(timezone.utc).isoformat()


def normalize_role(value: str) -> str:
    """Valida um papel de acesso conhecido."""
    normalized = value.strip().lower()
    if normalized not in ROLE_RANK:
        raise MiraiRuntimeError(
            f"papel inválido: {value!r}; use {', '.join(ACCESS_ROLES)}"
        )
    return normalized


def role_allows(actual: str, required: str) -> bool:
    """Compara os papéis segundo a hierarquia viewer < operator < admin."""
    return ROLE_RANK.get(actual, -1) >= ROLE_RANK[normalize_role(required)]


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
    atomic_write_bytes(path, content, mode)


def _save_private_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = (
        strict_json_dumps(payload, pretty=True) + "\n"
    ).encode("utf-8")
    _write_bytes_atomic(path, encoded, 0o600)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = strict_json_loads(path.read_bytes(), label=label)
    except (OSError, MiraiRuntimeError) as error:
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
        payload.get("version") not in {1, SECURITY_REGISTRY_VERSION}
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


def rotate_agent_identity(data_dir: Path, expected_agent_id: str) -> AgentIdentity:
    """Troca a identidade TLS e invalida clientes, exigindo confirmação exata."""
    root = data_dir.expanduser().resolve()
    current = load_or_create_identity(root)
    if not secrets.compare_digest(current.agent_id, expected_agent_id):
        raise MiraiRuntimeError(
            "confirmação incorreta; informe o Agent ID atual exatamente"
        )
    history = root / "identity-history"
    history.mkdir(parents=True, exist_ok=True)
    timestamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    audit_path = history / f"{timestamp}-{current.agent_id}.json"
    _save_private_json(
        audit_path,
        {
            "version": 1,
            "agent_id": current.agent_id,
            "fingerprint": current.fingerprint,
            "created_at": current.created_at,
            "rotated_at": utc_iso(),
        },
    )
    paths = (
        root / "identity.json",
        root / "agent-cert.pem",
        root / "agent-key.pem",
        root / "clients.json",
    )
    moved: list[tuple[Path, Path]] = []
    transaction_id = uuid.uuid4().hex
    try:
        for path in paths:
            if not path.exists():
                continue
            tombstone = root / f".{path.name}.rotate-{transaction_id}"
            os.replace(path, tombstone)
            moved.append((path, tombstone))
        rotated = _generate_identity(root)
    except BaseException:
        for generated in paths[:3]:
            generated.unlink(missing_ok=True)
        for original, tombstone in reversed(moved):
            if tombstone.exists():
                os.replace(tombstone, original)
        raise
    for _, tombstone in moved:
        try:
            tombstone.unlink(missing_ok=True)
        except OSError as error:
            raise MiraiRuntimeError(
                f"não foi possível remover material da identidade antiga: {error}"
            ) from error
    return rotated


class AgentSecurity:
    """Gerencia o canal seguro e os clientes pareados de um Agent."""

    def __init__(
        self,
        data_dir: Path,
        *,
        secure: bool,
        clock: Callable[[], datetime] = utc_now,
        pairing_role: str = "admin",
    ) -> None:
        self.secure = secure
        self.clients_path = data_dir / "clients.json"
        self._clock = clock
        self.pairing_role = normalize_role(pairing_role)
        self._lock = threading.Lock()
        self.identity = load_or_create_identity(data_dir) if secure else None
        self._pairing_code: str | None = None
        self._pairing_code_hash: str | None = None
        self._pairing_expires_at: datetime | None = None
        self._pairing_failures: dict[str, list[datetime]] = {}
        self._pairing_blocked_until: dict[str, datetime] = {}
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
            payload.get("version") not in {1, SECURITY_REGISTRY_VERSION}
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
            role = client.get("role", "admin")
            if role not in ROLE_RANK:
                raise MiraiRuntimeError(
                    "registro de clientes pareados possui papel inválido"
                )
            client["role"] = role
        payload["version"] = SECURITY_REGISTRY_VERSION
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

    def _check_pairing_limit_unlocked(self, peer_id: str) -> None:
        now = self._clock()
        blocked_until = self._pairing_blocked_until.get(peer_id)
        if blocked_until is not None and now < blocked_until:
            retry_after = max(1, int((blocked_until - now).total_seconds()))
            raise PairingRateLimited(
                "muitas tentativas de pareamento; aguarde antes de tentar novamente",
                retry_after,
            )
        if blocked_until is not None:
            self._pairing_blocked_until.pop(peer_id, None)
        cutoff = now - timedelta(seconds=PAIRING_ATTEMPT_WINDOW_SECONDS)
        self._pairing_failures[peer_id] = [
            moment
            for moment in self._pairing_failures.get(peer_id, [])
            if moment > cutoff
        ]

    def _record_pairing_failure_unlocked(self, peer_id: str) -> None:
        failures = self._pairing_failures.setdefault(peer_id, [])
        failures.append(self._clock())
        if len(failures) >= PAIRING_MAX_FAILURES:
            self._pairing_blocked_until[peer_id] = self._clock() + timedelta(
                seconds=PAIRING_BLOCK_SECONDS
            )

    def pair(
        self,
        name: str,
        code: str,
        *,
        peer_id: str = "unknown",
    ) -> dict[str, Any]:
        """Consome o código efêmero e devolve o token apenas uma vez."""
        if not self.secure or self.identity is None:
            raise PairingDenied("pareamento exige um Agent com HTTPS")
        with self._lock:
            normalized_peer = peer_id[:128] or "unknown"
            self._check_pairing_limit_unlocked(normalized_peer)
            if not CLIENT_NAME_PATTERN.fullmatch(name):
                self._record_pairing_failure_unlocked(normalized_peer)
                raise PairingDenied(
                    "nome do cliente inválido; use letras, números, ponto, "
                    "hífen ou sublinhado"
                )
            try:
                normalized_code = normalize_pairing_code(code)
            except PairingDenied:
                self._record_pairing_failure_unlocked(normalized_peer)
                raise
            if (
                not self._pairing_code_hash
                or not self._pairing_expires_at
                or self._clock() >= self._pairing_expires_at
            ):
                self._record_pairing_failure_unlocked(normalized_peer)
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
                self._record_pairing_failure_unlocked(normalized_peer)
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
                "role": self.pairing_role,
            }
            payload = self._load_clients_unlocked()
            payload["clients"].append(client)
            self._save_clients_unlocked(payload)
            self._pairing_code = None
            self._pairing_code_hash = None
            self._pairing_expires_at = None
            self._pairing_failures.pop(normalized_peer, None)
            self._pairing_blocked_until.pop(normalized_peer, None)

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
                "role": "admin",
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

    def set_client_role(self, client_id: str, role: str) -> dict[str, Any]:
        """Altera um papel sem permitir que o último admin seja removido."""
        if not CLIENT_ID_PATTERN.fullmatch(client_id):
            raise MiraiRuntimeError("identificador de cliente inválido")
        normalized_role = normalize_role(role)
        with self._lock:
            payload = self._load_clients_unlocked()
            client = next(
                (
                    item
                    for item in payload["clients"]
                    if item.get("client_id") == client_id
                ),
                None,
            )
            if client is None:
                raise MiraiRuntimeError(f"cliente '{client_id}' não encontrado")
            admin_count = sum(
                item.get("role") == "admin" for item in payload["clients"]
            )
            if (
                client.get("role") == "admin"
                and normalized_role != "admin"
                and admin_count <= 1
            ):
                raise MiraiRuntimeError(
                    "o último administrador não pode ser rebaixado"
                )
            client["role"] = normalized_role
            client["updated_at"] = utc_iso(self._clock())
            self._save_clients_unlocked(payload)
            return self._public_client(client)

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
