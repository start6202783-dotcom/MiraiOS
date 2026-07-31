"""Empacotamento e materialização efêmera de entradas remotas."""

from __future__ import annotations

import base64
import hashlib
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .errors import MiraiRuntimeError
from .json_codec import strict_json_dumps, strict_json_loads

ATTACHMENT_EXTENSIONS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".json": "application/json",
    ".npy": "application/x-npy",
}
MAX_ATTACHMENT_COUNT = 16
MAX_ATTACHMENT_SIZE_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENTS_TOTAL_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
ATTACHMENT_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _split_spec(spec: str) -> tuple[str | None, str]:
    possible_name, separator, value = spec.partition("=")
    return (possible_name, value) if separator else (None, spec)


def encode_remote_inputs(
    input_specs: list[str] | None,
) -> tuple[list[str] | None, list[dict[str, Any]]]:
    """Substitui arquivos locais por referências e anexos com hash."""
    if input_specs is None:
        return None, []
    rewritten = list(input_specs)
    attachments: list[dict[str, Any]] = []
    total_size = 0
    for index, spec in enumerate(input_specs):
        input_name, raw_value = _split_spec(spec)
        candidate = Path(raw_value).expanduser()
        suffix = candidate.suffix.lower()
        if suffix not in ATTACHMENT_EXTENSIONS:
            continue
        if not candidate.is_file():
            raise MiraiRuntimeError(f"arquivo de entrada não encontrado: {candidate}")
        size = candidate.stat().st_size
        if size <= 0:
            raise MiraiRuntimeError(f"arquivo de entrada está vazio: {candidate}")
        if size > MAX_ATTACHMENT_SIZE_BYTES:
            raise MiraiRuntimeError(
                f"arquivo de entrada excede {MAX_ATTACHMENT_SIZE_BYTES // (1024 * 1024)} MB"
            )
        total_size += size
        if total_size > MAX_ATTACHMENTS_TOTAL_BYTES:
            raise MiraiRuntimeError("anexos remotos excedem o limite total de 10 MB")
        if len(attachments) >= MAX_ATTACHMENT_COUNT:
            raise MiraiRuntimeError("a inferência remota aceita no máximo 16 anexos")
        try:
            content = candidate.read_bytes()
        except OSError as error:
            raise MiraiRuntimeError(
                f"não foi possível ler o arquivo de entrada: {error}"
            ) from error
        if len(content) != size:
            raise MiraiRuntimeError(
                f"arquivo de entrada foi alterado durante a leitura: {candidate}"
            )
        attachment_id = hashlib.sha256(
            f"{index}:".encode("ascii") + content
        ).hexdigest()[:16]
        reference = f"@attachment:{attachment_id}"
        rewritten[index] = (
            f"{input_name}={reference}" if input_name is not None else reference
        )
        attachments.append(
            {
                "id": attachment_id,
                "name": candidate.name,
                "media_type": ATTACHMENT_EXTENSIONS[suffix],
                "encoding": "base64",
                "size_bytes": size,
                "sha256": hashlib.sha256(content).hexdigest(),
                "data": base64.b64encode(content).decode("ascii"),
            }
        )
    return rewritten, attachments


def _strict_json(content: bytes) -> str:
    parsed = strict_json_loads(content, label="anexo JSON")
    return strict_json_dumps(parsed)


def _validate_image(path: Path, expected_media_type: str) -> None:
    try:
        from PIL import Image, UnidentifiedImageError
    except ModuleNotFoundError as error:
        raise MiraiRuntimeError(
            "a dependência 'Pillow' é necessária para validar imagens"
        ) from error
    expected_format = {
        "image/png": {"PNG"},
        "image/jpeg": {"JPEG"},
        "image/bmp": {"BMP"},
        "image/webp": {"WEBP"},
    }[expected_media_type]
    try:
        with Image.open(path) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise MiraiRuntimeError(
                    "imagem excede o limite de 50 milhões de pixels"
                )
            image.verify()
            actual_format = image.format
    except MiraiRuntimeError:
        raise
    except (OSError, ValueError, UnidentifiedImageError) as error:
        raise MiraiRuntimeError(f"anexo de imagem inválido: {error}") from error
    if actual_format not in expected_format:
        raise MiraiRuntimeError(
            f"conteúdo da imagem não corresponde ao tipo {expected_media_type}"
        )


def _validate_npy(path: Path) -> None:
    try:
        import numpy as np
    except ModuleNotFoundError as error:
        raise MiraiRuntimeError("a dependência 'numpy' é necessária para anexos NPY") from error
    try:
        array = np.load(path, allow_pickle=False)
    except (OSError, ValueError) as error:
        raise MiraiRuntimeError(f"anexo NPY inválido ou inseguro: {error}") from error
    if array.dtype.hasobject:
        raise MiraiRuntimeError("anexos NPY com objetos não são permitidos")
    if array.ndim > 16:
        raise MiraiRuntimeError("anexo NPY excede o limite de 16 dimensões")


def _decode_attachment(item: Any, directory: Path) -> tuple[str, str]:
    if not isinstance(item, dict) or set(item) != {
        "id",
        "name",
        "media_type",
        "encoding",
        "size_bytes",
        "sha256",
        "data",
    }:
        raise MiraiRuntimeError("descritor de anexo possui campos incompatíveis")
    attachment_id = item["id"]
    name = item["name"]
    media_type = item["media_type"]
    size = item["size_bytes"]
    digest = item["sha256"]
    if not isinstance(attachment_id, str) or not ATTACHMENT_ID_PATTERN.fullmatch(attachment_id):
        raise MiraiRuntimeError("identificador de anexo inválido")
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 255
        or Path(name).name != name
        or "/" in name
        or "\\" in name
    ):
        raise MiraiRuntimeError("nome de anexo inválido")
    suffix = Path(name).suffix.lower()
    if suffix not in ATTACHMENT_EXTENSIONS:
        raise MiraiRuntimeError("extensão de anexo não permitida")
    if media_type != ATTACHMENT_EXTENSIONS[suffix]:
        raise MiraiRuntimeError("tipo de mídia não corresponde à extensão do anexo")
    if item["encoding"] != "base64":
        raise MiraiRuntimeError("encoding de anexo não suportado")
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or not 0 < size <= MAX_ATTACHMENT_SIZE_BYTES
    ):
        raise MiraiRuntimeError("tamanho declarado do anexo é inválido")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise MiraiRuntimeError("SHA-256 declarado do anexo é inválido")
    if not isinstance(item["data"], str):
        raise MiraiRuntimeError("dados do anexo devem usar base64")
    try:
        content = base64.b64decode(item["data"], validate=True)
    except (ValueError, TypeError) as error:
        raise MiraiRuntimeError("dados do anexo não são base64 válido") from error
    if len(content) != size:
        raise MiraiRuntimeError("tamanho real do anexo não corresponde ao declarado")
    if hashlib.sha256(content).hexdigest() != digest:
        raise MiraiRuntimeError("SHA-256 do anexo não confere")

    if suffix == ".json":
        return attachment_id, _strict_json(content)

    target = directory / f"{attachment_id}{suffix}"
    try:
        target.write_bytes(content)
    except OSError as error:
        raise MiraiRuntimeError(f"não foi possível materializar o anexo: {error}") from error
    if media_type.startswith("image/"):
        _validate_image(target, media_type)
    elif suffix == ".npy":
        _validate_npy(target)
    return attachment_id, str(target)


@contextmanager
def materialize_remote_inputs(
    input_specs: list[str] | None,
    attachments: Any,
) -> Iterator[list[str] | None]:
    """Valida anexos, troca referências e elimina os arquivos ao terminar."""
    if attachments is None:
        attachments = []
    if not isinstance(attachments, list) or len(attachments) > MAX_ATTACHMENT_COUNT:
        raise MiraiRuntimeError("'attachments' deve ser uma lista de até 16 itens")
    with tempfile.TemporaryDirectory(prefix="mirai-input-") as temporary:
        directory = Path(temporary)
        resolved: dict[str, str] = {}
        total = 0
        for item in attachments:
            attachment_id, value = _decode_attachment(item, directory)
            if attachment_id in resolved:
                raise MiraiRuntimeError("identificador de anexo duplicado")
            total += int(item["size_bytes"])
            if total > MAX_ATTACHMENTS_TOTAL_BYTES:
                raise MiraiRuntimeError("anexos remotos excedem o limite total de 10 MB")
            resolved[attachment_id] = value

        rewritten: list[str] | None = None if input_specs is None else []
        used: set[str] = set()
        for spec in input_specs or []:
            input_name, raw_value = _split_spec(spec)
            if raw_value.startswith("@attachment:"):
                attachment_id = raw_value.removeprefix("@attachment:")
                if attachment_id not in resolved:
                    raise MiraiRuntimeError("referência aponta para anexo ausente")
                raw_value = resolved[attachment_id]
                used.add(attachment_id)
            elif Path(raw_value).suffix.lower() in ATTACHMENT_EXTENSIONS:
                raise MiraiRuntimeError(
                    "arquivos remotos devem ser enviados como anexos validados"
                )
            if rewritten is None:
                raise MiraiRuntimeError("lista de entradas remotas inconsistente")
            rewritten.append(
                f"{input_name}={raw_value}" if input_name is not None else raw_value
            )
        unused = set(resolved) - used
        if unused:
            raise MiraiRuntimeError("a requisição contém anexos que não foram referenciados")
        yield rewritten
