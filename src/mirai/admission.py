"""Política de admissão para impedir deploys não autorizados no Agent."""

from __future__ import annotations

import base64
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import MiraiRuntimeError
from .package import MIRAI_EXTENSION
from .signing import MAX_SIGNATURE_SIZE_BYTES, verify_artifact

ADMISSION_MODES = ("open", "signed")
MAX_SIGNATURE_HEADER_CHARS = 32 * 1024


@dataclass(frozen=True, slots=True)
class AdmissionPolicy:
    """Configuração local; um cliente remoto nunca consegue afrouxá-la."""

    mode: str = "open"
    trusted_keys: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if self.mode not in ADMISSION_MODES:
            raise MiraiRuntimeError(
                f"modo de admissão inválido; use {', '.join(ADMISSION_MODES)}"
            )
        normalized: list[Path] = []
        for key in self.trusted_keys:
            target = key.expanduser().resolve()
            if not target.is_file():
                raise MiraiRuntimeError(f"chave pública confiável não encontrada: {target}")
            if target.stat().st_size > MAX_SIGNATURE_SIZE_BYTES:
                raise MiraiRuntimeError(f"chave pública confiável é grande demais: {target}")
            normalized.append(target)
        if self.mode == "signed" and not normalized:
            raise MiraiRuntimeError(
                "admissão signed exige ao menos uma opção --trust-key"
            )
        object.__setattr__(self, "trusted_keys", tuple(normalized))


def _decode_signature(value: str | None) -> bytes | None:
    if value is None:
        return None
    if not value or len(value) > MAX_SIGNATURE_HEADER_CHARS:
        raise MiraiRuntimeError("assinatura de admissão ausente ou grande demais")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (TypeError, ValueError) as error:
        raise MiraiRuntimeError("assinatura de admissão não é base64 válido") from error
    if not decoded or len(decoded) > MAX_SIGNATURE_SIZE_BYTES:
        raise MiraiRuntimeError("assinatura de admissão possui tamanho inválido")
    return decoded


def admit_artifact(
    artifact_path: Path,
    signature_header: str | None,
    policy: AdmissionPolicy,
    *,
    artifact_name: str | None = None,
) -> dict[str, str | bool | None]:
    """Aplica fail-closed: assinatura apresentada e inválida nunca é ignorada."""
    signature = _decode_signature(signature_header)
    if policy.mode == "signed":
        if artifact_path.suffix.lower() != MIRAI_EXTENSION:
            raise MiraiRuntimeError(
                "a política signed aceita somente pacotes .mirai assinados"
            )
        if signature is None:
            raise MiraiRuntimeError(
                "o Agent exige uma assinatura DSSE para este deployment"
            )
    if signature is None:
        return {"mode": policy.mode, "verified": False, "key_id": None}
    if not policy.trusted_keys:
        raise MiraiRuntimeError(
            "uma assinatura foi enviada, mas o Agent não possui chaves confiáveis"
        )

    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="mirai-admission-") as directory:
        signature_path = Path(directory) / "artifact.sig"
        signature_path.write_bytes(signature)
        for public_key in policy.trusted_keys:
            try:
                verification = verify_artifact(
                    artifact_path,
                    signature_path,
                    public_key,
                    artifact_name=artifact_name or artifact_path.name,
                )
            except MiraiRuntimeError as error:
                errors.append(str(error))
                continue
            return {
                "mode": policy.mode,
                "verified": True,
                "key_id": str(verification["key_id"]),
            }
    raise MiraiRuntimeError(
        "assinatura não foi validada por nenhuma chave confiável"
        + (f": {errors[-1]}" if errors else "")
    )
