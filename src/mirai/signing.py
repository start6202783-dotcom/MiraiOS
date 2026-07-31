"""Assinaturas Ed25519 em envelopes DSSE para artefatos MiraiOS."""

from __future__ import annotations

import base64
import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .errors import MiraiRuntimeError
from .json_codec import canonical_json_bytes, strict_json_dumps, strict_json_loads
from .storage import atomic_write_bytes, stable_file_digest

DSSE_PAYLOAD_TYPE = "application/vnd.mirai.artifact-digest.v1+json"
SIGNATURE_SUFFIX = ".sig"
KEY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_SIGNATURE_SIZE_BYTES = 64 * 1024


def _crypto() -> tuple[Any, Any, Any]:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ModuleNotFoundError as error:
        raise MiraiRuntimeError(
            "a dependência 'cryptography' é necessária para assinar artefatos"
        ) from error
    return Ed25519PrivateKey, serialization, InvalidSignature


def _write_atomic(path: Path, content: bytes, mode: int) -> None:
    atomic_write_bytes(path, content, mode)


def signing_key_paths(home: Path, name: str) -> tuple[Path, Path]:
    """Retorna caminhos previsíveis sem aceitar travessia de diretório."""
    if not KEY_NAME_PATTERN.fullmatch(name):
        raise MiraiRuntimeError(
            "nome de chave inválido; use letras, números, ponto, hífen ou sublinhado"
        )
    directory = home.expanduser().resolve() / "keys"
    return directory / f"{name}.key", directory / f"{name}.pub"


def generate_signing_key(
    private_path: Path,
    public_path: Path | None = None,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Gera um par Ed25519; a chave privada é gravada com modo 0600."""
    Ed25519PrivateKey, serialization, _ = _crypto()
    private_target = private_path.expanduser().resolve()
    public_target = (
        public_path.expanduser().resolve()
        if public_path is not None
        else private_target.with_suffix(".pub")
    )
    if private_target == public_target:
        raise MiraiRuntimeError("as chaves privada e pública devem usar arquivos distintos")
    if not replace and (private_target.exists() or public_target.exists()):
        raise MiraiRuntimeError("a chave já existe; use --replace para substituí-la")

    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    raw_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    try:
        _write_atomic(private_target, private_bytes, 0o600)
        _write_atomic(public_target, public_bytes, 0o644)
    except OSError as error:
        raise MiraiRuntimeError(f"não foi possível gravar a chave: {error}") from error
    return {
        "private_key": private_target,
        "public_key": public_target,
        "key_id": hashlib.sha256(raw_public).hexdigest(),
    }


def _load_private_key(path: Path) -> Any:
    _, serialization, _ = _crypto()
    target = path.expanduser()
    if os.name != "nt":
        try:
            if target.stat().st_mode & 0o077:
                raise MiraiRuntimeError(
                    "chave privada possui permissões inseguras; use chmod 600"
                )
        except OSError as error:
            raise MiraiRuntimeError(
                f"não foi possível inspecionar a chave privada: {error}"
            ) from error
    try:
        key = serialization.load_pem_private_key(
            target.read_bytes(),
            password=None,
        )
    except (OSError, ValueError, TypeError) as error:
        raise MiraiRuntimeError(f"chave privada Ed25519 inválida: {error}") from error
    if key.__class__.__name__ != "Ed25519PrivateKey":
        raise MiraiRuntimeError("a chave privada não é Ed25519")
    return key


def _load_public_key(path: Path) -> Any:
    _, serialization, _ = _crypto()
    try:
        key = serialization.load_pem_public_key(path.expanduser().read_bytes())
    except (OSError, ValueError, TypeError) as error:
        raise MiraiRuntimeError(f"chave pública Ed25519 inválida: {error}") from error
    if key.__class__.__name__ != "Ed25519PublicKey":
        raise MiraiRuntimeError("a chave pública não é Ed25519")
    return key


def _raw_public(key: Any) -> bytes:
    _, serialization, _ = _crypto()
    return key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _pae(payload_type: str, payload: bytes) -> bytes:
    """Implementa o Pre-Authentication Encoding definido pelo DSSE."""
    type_bytes = payload_type.encode("utf-8")
    return b" ".join(
        (
            b"DSSEv1",
            str(len(type_bytes)).encode("ascii"),
            type_bytes,
            str(len(payload)).encode("ascii"),
            payload,
        )
    )


def _artifact_payload(
    path: Path,
    *,
    artifact_name: str | None = None,
) -> dict[str, Any]:
    target = path.expanduser().resolve()
    if not target.is_file():
        raise MiraiRuntimeError(f"artefato não encontrado: {target}")
    if target.suffix.lower() not in {".mirai", ".json"}:
        raise MiraiRuntimeError(
            "somente pacotes .mirai e relatórios JSON podem ser assinados"
        )
    digest, size_bytes = stable_file_digest(target)
    kind = "mirai-package" if target.suffix.lower() == ".mirai" else "pilot-report"
    return {
        "version": 1,
        "kind": kind,
        "name": artifact_name or target.name,
        "sha256": digest,
        "size_bytes": size_bytes,
        "signed_at": datetime.now(timezone.utc).isoformat(),
    }


def sign_artifact(
    artifact_path: Path,
    private_key_path: Path,
    signature_path: Path | None = None,
    *,
    replace: bool = False,
) -> dict[str, Any]:
    """Assina o digest tipado de um pacote ou relatório sem alterá-lo."""
    target = artifact_path.expanduser().resolve()
    signature_target = (
        signature_path.expanduser().resolve()
        if signature_path is not None
        else target.with_name(target.name + SIGNATURE_SUFFIX)
    )
    if signature_target.exists() and not replace:
        raise MiraiRuntimeError(
            f"assinatura já existe: {signature_target}; use --replace"
        )
    if signature_target == target:
        raise MiraiRuntimeError("a assinatura não pode substituir o próprio artefato")
    private_key = _load_private_key(private_key_path)
    payload_data = _artifact_payload(target)
    payload = canonical_json_bytes(payload_data)
    signature = private_key.sign(_pae(DSSE_PAYLOAD_TYPE, payload))
    key_id = hashlib.sha256(_raw_public(private_key.public_key())).hexdigest()
    envelope = {
        "payloadType": DSSE_PAYLOAD_TYPE,
        "payload": base64.b64encode(payload).decode("ascii"),
        "signatures": [
            {
                "keyid": key_id,
                "sig": base64.b64encode(signature).decode("ascii"),
            }
        ],
    }
    encoded = (
        strict_json_dumps(envelope, pretty=True) + "\n"
    ).encode("utf-8")
    try:
        _write_atomic(signature_target, encoded, 0o644)
    except OSError as error:
        raise MiraiRuntimeError(f"não foi possível gravar a assinatura: {error}") from error
    return {
        "signature": signature_target,
        "key_id": key_id,
        "payload": payload_data,
    }


def _decode_base64(value: Any, label: str) -> bytes:
    if not isinstance(value, str):
        raise MiraiRuntimeError(f"{label} DSSE inválido")
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as error:
        raise MiraiRuntimeError(f"{label} DSSE não é base64 válido") from error


def verify_artifact(
    artifact_path: Path,
    signature_path: Path,
    public_key_path: Path,
    *,
    artifact_name: str | None = None,
) -> dict[str, Any]:
    """Verifica envelope, identidade da chave e digest do arquivo atual."""
    target = signature_path.expanduser().resolve()
    if not target.is_file():
        raise MiraiRuntimeError(f"assinatura não encontrada: {target}")
    if target.stat().st_size > MAX_SIGNATURE_SIZE_BYTES:
        raise MiraiRuntimeError("assinatura excede o limite de 64 KB")
    try:
        envelope = strict_json_loads(target.read_bytes(), label="envelope DSSE")
    except (OSError, MiraiRuntimeError) as error:
        raise MiraiRuntimeError(f"envelope DSSE inválido: {error}") from error
    if not isinstance(envelope, dict) or set(envelope) != {
        "payloadType",
        "payload",
        "signatures",
    }:
        raise MiraiRuntimeError("envelope DSSE possui campos incompatíveis")
    if envelope["payloadType"] != DSSE_PAYLOAD_TYPE:
        raise MiraiRuntimeError("tipo de payload DSSE incompatível")
    signatures = envelope.get("signatures")
    if not isinstance(signatures, list) or len(signatures) != 1:
        raise MiraiRuntimeError("envelope DSSE deve conter exatamente uma assinatura")
    signature_entry = signatures[0]
    if not isinstance(signature_entry, dict) or set(signature_entry) != {"keyid", "sig"}:
        raise MiraiRuntimeError("entrada de assinatura DSSE inválida")
    payload = _decode_base64(envelope["payload"], "payload")
    signature = _decode_base64(signature_entry["sig"], "assinatura")
    public_key = _load_public_key(public_key_path)
    key_id = hashlib.sha256(_raw_public(public_key)).hexdigest()
    if signature_entry["keyid"] != key_id:
        raise MiraiRuntimeError("a assinatura não pertence à chave pública informada")
    _, _, InvalidSignature = _crypto()
    try:
        public_key.verify(signature, _pae(DSSE_PAYLOAD_TYPE, payload))
    except InvalidSignature as error:
        raise MiraiRuntimeError("assinatura Ed25519 inválida") from error
    try:
        signed_payload = strict_json_loads(payload, label="payload assinado")
    except MiraiRuntimeError as error:
        raise MiraiRuntimeError("payload assinado não é JSON válido") from error
    if not isinstance(signed_payload, dict):
        raise MiraiRuntimeError("payload assinado possui formato inválido")
    actual = _artifact_payload(artifact_path, artifact_name=artifact_name)
    for field in ("version", "kind", "name", "sha256", "size_bytes"):
        if signed_payload.get(field) != actual[field]:
            raise MiraiRuntimeError(f"artefato não corresponde à assinatura ({field})")
    return {
        "valid": True,
        "key_id": key_id,
        "payload": signed_payload,
    }
