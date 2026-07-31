"""Primitivas duráveis para arquivos que representam estado ou confiança."""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path

from .errors import MiraiRuntimeError

READ_CHUNK_BYTES = 1024 * 1024


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, content: bytes, mode: int = 0o600) -> None:
    """Grava, sincroniza e troca o arquivo sem reutilizar nomes temporários."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
        _fsync_directory(path.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_text(
    path: Path,
    content: str,
    *,
    mode: int = 0o600,
) -> None:
    atomic_write_bytes(path, content.encode("utf-8"), mode)


def stable_file_digest(path: Path) -> tuple[str, int]:
    """Calcula SHA-256 e recusa arquivos trocados ou alterados durante a leitura."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise MiraiRuntimeError(f"não foi possível abrir '{path}': {error}") from error
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            for chunk in iter(lambda: source.read(READ_CHUNK_BYTES), b""):
                digest.update(chunk)
            after = os.fstat(source.fileno())
    except OSError as error:
        raise MiraiRuntimeError(f"não foi possível ler '{path}': {error}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    stable = (
        before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_size == after.st_size
        and before.st_mtime_ns == after.st_mtime_ns
        and before.st_ctime_ns == after.st_ctime_ns
    )
    if not stable:
        raise MiraiRuntimeError(f"arquivo foi alterado durante a leitura: {path}")
    return digest.hexdigest(), after.st_size


def verify_file(
    path: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    label: str,
) -> None:
    actual_sha256, actual_size = stable_file_digest(path)
    if actual_size != expected_size:
        raise MiraiRuntimeError(
            f"{label} foi alterado: tamanho {actual_size}, esperado {expected_size}"
        )
    if not secrets.compare_digest(actual_sha256, expected_sha256):
        raise MiraiRuntimeError(f"{label} foi alterado: SHA-256 não confere")
