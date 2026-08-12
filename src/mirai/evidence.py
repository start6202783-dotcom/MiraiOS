"""Evidence Bundle portátil com conteúdo, digests e origem verificáveis."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from . import __version__
from .errors import MiraiRuntimeError
from .inspect import validate_artifact
from .json_codec import canonical_json_bytes, strict_json_dumps, strict_json_loads
from .passport import load_model_passport
from .storage import atomic_write_text, stable_file_digest

EVIDENCE_FORMAT = "mirai.evidence-bundle"
EVIDENCE_VERSION = 1
EVIDENCE_EXTENSION = ".mirai-evidence"
MAX_EVIDENCE_ITEM_BYTES = 16 * 1024 * 1024
MAX_EVIDENCE_TOTAL_BYTES = 32 * 1024 * 1024
MAX_EVIDENCE_BUNDLE_BYTES = 48 * 1024 * 1024
MAX_EVIDENCE_ITEMS = 32


def _media_type(path: Path) -> str:
    known = {
        ".json": "application/json",
        ".md": "text/markdown",
        ".sig": "application/vnd.dsse.envelope.v1+json",
    }
    return known.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or (
        "application/octet-stream"
    )


def _evidence_item(path: Path, role: str) -> dict[str, Any]:
    target = path.expanduser().resolve()
    try:
        declared_size = target.stat().st_size
        if not 0 < declared_size <= MAX_EVIDENCE_ITEM_BYTES:
            raise MiraiRuntimeError("item de evidência vazio ou acima de 16 MB")
        content = target.read_bytes()
    except OSError as error:
        raise MiraiRuntimeError(f"não foi possível ler a evidência: {error}") from error
    if len(content) != declared_size:
        raise MiraiRuntimeError("item de evidência foi alterado durante a leitura")
    if "/" in target.name or "\\" in target.name:
        raise MiraiRuntimeError("nome de evidência não é portátil")
    return {
        "role": role,
        "name": target.name,
        "media_type": _media_type(target),
        "encoding": "base64",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "data": base64.b64encode(content).decode("ascii"),
    }


def _companion_paths(report: Path) -> list[tuple[Path, str]]:
    candidates = [
        (report.with_suffix(".md"), "report-markdown"),
        (report.with_name(report.name + ".sig"), "report-signature"),
    ]
    return [(path, role) for path, role in candidates if path.is_file()]


def create_evidence_bundle(
    report_path: Path,
    output_path: Path,
    *,
    artifact_path: Path | None = None,
    passport_path: Path | None = None,
    include_paths: Iterable[Path] = (),
    include_companions: bool = True,
    replace: bool = False,
) -> dict[str, Any]:
    """Agrupa um relatório e evidências opcionais sem incluir o modelo."""
    report = report_path.expanduser().resolve()
    output = output_path.expanduser().resolve()
    if report.suffix.lower() != ".json":
        raise MiraiRuntimeError("o relatório principal deve ser JSON")
    if output.suffix.lower() != EVIDENCE_EXTENSION:
        raise MiraiRuntimeError(
            f"o Evidence Bundle deve usar a extensão {EVIDENCE_EXTENSION}"
        )
    if output.exists() and not replace:
        raise MiraiRuntimeError(f"arquivo de saída já existe: {output}")
    try:
        report_size = report.stat().st_size
        if not 0 < report_size <= MAX_EVIDENCE_ITEM_BYTES:
            raise MiraiRuntimeError("relatório principal vazio ou acima de 16 MB")
        report_content = report.read_bytes()
    except OSError as error:
        raise MiraiRuntimeError(f"não foi possível ler o relatório: {error}") from error
    report_data = strict_json_loads(report_content, label="relatório principal")
    if not isinstance(report_data, dict):
        raise MiraiRuntimeError("o relatório principal deve ser um objeto JSON")

    subject: dict[str, Any] | None = None
    artifact: Path | None = None
    if artifact_path is not None:
        artifact = artifact_path.expanduser().resolve()
        validate_artifact(artifact)
        digest, size = stable_file_digest(artifact)
        subject = {
            "name": artifact.name,
            "sha256": digest,
            "size_bytes": size,
            "included": False,
        }

    selected: list[tuple[Path, str]] = [(report, "report-json")]
    if include_companions:
        selected.extend(_companion_paths(report))
    if passport_path is not None:
        passport = passport_path.expanduser().resolve()
        passport_data = load_model_passport(passport)
        if subject is not None:
            passport_subject = passport_data["subject"]
            if (
                passport_subject["sha256"] != subject["sha256"]
                or passport_subject["size_bytes"] != subject["size_bytes"]
            ):
                raise MiraiRuntimeError(
                    "o Model Passport não corresponde ao artefato do bundle"
                )
        selected.append((passport, "model-passport"))
    for index, path in enumerate(include_paths):
        selected.append((path.expanduser().resolve(), f"supporting-{index + 1}"))
    if len(selected) > MAX_EVIDENCE_ITEMS:
        raise MiraiRuntimeError(
            f"o Evidence Bundle aceita no máximo {MAX_EVIDENCE_ITEMS} itens"
        )
    resolved_paths = [path for path, _ in selected]
    if output in resolved_paths:
        raise MiraiRuntimeError("o Evidence Bundle não pode incluir o próprio destino")
    if len(set(resolved_paths)) != len(resolved_paths):
        raise MiraiRuntimeError("a mesma evidência foi incluída mais de uma vez")

    items = [_evidence_item(path, role) for path, role in selected]
    total_bytes = sum(int(item["size_bytes"]) for item in items)
    if total_bytes > MAX_EVIDENCE_TOTAL_BYTES:
        raise MiraiRuntimeError("evidências excedem o limite total de 32 MB")
    descriptors = [
        {key: value for key, value in item.items() if key != "data"}
        for item in items
    ]
    bundle: dict[str, Any] = {
        "format": EVIDENCE_FORMAT,
        "schema_version": EVIDENCE_VERSION,
        "subject": subject,
        "root_report": {
            "role": "report-json",
            "sha256": items[0]["sha256"],
        },
        "manifest": {
            "item_count": len(items),
            "total_bytes": total_bytes,
            "items": descriptors,
        },
        "items": items,
        "claims": {
            "contents_verified_by_digest": True,
            "artifact_bytes_included": False,
            "independent_certification": False,
        },
        "created_by": {"tool": "miraios", "version": __version__},
    }
    bundle["bundle_id"] = "evidence-" + hashlib.sha256(
        canonical_json_bytes(bundle)
    ).hexdigest()[:24]
    try:
        atomic_write_text(
            output,
            strict_json_dumps(bundle, pretty=True, sort_keys=True) + "\n",
            mode=0o600,
        )
    except OSError as error:
        raise MiraiRuntimeError(
            f"não foi possível gravar o Evidence Bundle: {error}"
        ) from error
    return bundle


def load_evidence_bundle(path: Path) -> dict[str, Any]:
    target = path.expanduser().resolve()
    if target.suffix.lower() != EVIDENCE_EXTENSION:
        raise MiraiRuntimeError(
            f"o Evidence Bundle deve usar a extensão {EVIDENCE_EXTENSION}"
        )
    try:
        size = target.stat().st_size
        content = target.read_bytes()
    except OSError as error:
        raise MiraiRuntimeError(
            f"não foi possível ler o Evidence Bundle: {error}"
        ) from error
    if not 0 < size <= MAX_EVIDENCE_BUNDLE_BYTES:
        raise MiraiRuntimeError("Evidence Bundle vazio ou acima de 48 MB")
    bundle = strict_json_loads(
        content,
        label="Evidence Bundle",
        max_string_chars=MAX_EVIDENCE_BUNDLE_BYTES,
    )
    if not isinstance(bundle, dict):
        raise MiraiRuntimeError("Evidence Bundle deve ser um objeto JSON")
    required = {
        "format",
        "schema_version",
        "subject",
        "root_report",
        "manifest",
        "items",
        "claims",
        "created_by",
        "bundle_id",
    }
    if set(bundle) != required:
        raise MiraiRuntimeError("Evidence Bundle possui campos incompatíveis")
    if (
        bundle["format"] != EVIDENCE_FORMAT
        or bundle["schema_version"] != EVIDENCE_VERSION
    ):
        raise MiraiRuntimeError("versão do Evidence Bundle não suportada")
    subject = bundle.get("subject")
    if subject is not None:
        if not isinstance(subject, dict) or set(subject) != {
            "name",
            "sha256",
            "size_bytes",
            "included",
        }:
            raise MiraiRuntimeError("subject do Evidence Bundle inválido")
        digest = subject.get("sha256")
        size_bytes = subject.get("size_bytes")
        if (
            not isinstance(subject.get("name"), str)
            or Path(subject["name"]).name != subject["name"]
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes <= 0
            or subject.get("included") is not False
        ):
            raise MiraiRuntimeError("identidade do subject no Evidence Bundle inválida")
    items = bundle.get("items")
    manifest = bundle.get("manifest")
    if (
        not isinstance(items, list)
        or not 0 < len(items) <= MAX_EVIDENCE_ITEMS
        or not isinstance(manifest, dict)
    ):
        raise MiraiRuntimeError("manifesto do Evidence Bundle inválido")
    total = 0
    descriptors: list[dict[str, Any]] = []
    roles: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "role",
            "name",
            "media_type",
            "encoding",
            "size_bytes",
            "sha256",
            "data",
        }:
            raise MiraiRuntimeError("item do Evidence Bundle inválido")
        role = item.get("role")
        name = item.get("name")
        if (
            not isinstance(role, str)
            or not role
            or role in roles
            or not isinstance(name, str)
            or Path(name).name != name
            or "/" in name
            or "\\" in name
        ):
            raise MiraiRuntimeError("nome ou papel de evidência inválido")
        roles.add(role)
        size_bytes = item.get("size_bytes")
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or not 0 < size_bytes <= MAX_EVIDENCE_ITEM_BYTES
            or item.get("encoding") != "base64"
            or not isinstance(item.get("data"), str)
        ):
            raise MiraiRuntimeError("descritor de evidência inválido")
        try:
            decoded = base64.b64decode(item["data"], validate=True)
        except (TypeError, ValueError) as error:
            raise MiraiRuntimeError("evidência não usa base64 válido") from error
        if len(decoded) != size_bytes:
            raise MiraiRuntimeError("tamanho da evidência não corresponde ao manifesto")
        if hashlib.sha256(decoded).hexdigest() != item.get("sha256"):
            raise MiraiRuntimeError("SHA-256 da evidência não confere")
        total += size_bytes
        descriptors.append({key: value for key, value in item.items() if key != "data"})
        if role in {"report-json", "model-passport"}:
            parsed = strict_json_loads(decoded, label=role)
            if not isinstance(parsed, dict):
                raise MiraiRuntimeError(f"{role} deve ser um objeto JSON")
            if role == "model-passport" and parsed.get("format") != "mirai.model-passport":
                raise MiraiRuntimeError("Model Passport incorporado possui formato inválido")
    if total > MAX_EVIDENCE_TOTAL_BYTES:
        raise MiraiRuntimeError("evidências excedem o limite total de 32 MB")
    expected_manifest = {
        "item_count": len(items),
        "total_bytes": total,
        "items": descriptors,
    }
    if manifest != expected_manifest:
        raise MiraiRuntimeError("manifesto não corresponde às evidências")
    root = bundle.get("root_report")
    report_items = [item for item in items if item["role"] == "report-json"]
    if (
        len(report_items) != 1
        or not isinstance(root, dict)
        or root != {"role": "report-json", "sha256": report_items[0]["sha256"]}
    ):
        raise MiraiRuntimeError("relatório raiz do Evidence Bundle inválido")
    claimed_id = bundle.get("bundle_id")
    without_id = dict(bundle)
    del without_id["bundle_id"]
    expected_id = "evidence-" + hashlib.sha256(
        canonical_json_bytes(without_id)
    ).hexdigest()[:24]
    if claimed_id != expected_id:
        raise MiraiRuntimeError("identificador do Evidence Bundle não confere")
    claims = bundle.get("claims")
    if claims != {
        "contents_verified_by_digest": True,
        "artifact_bytes_included": False,
        "independent_certification": False,
    }:
        raise MiraiRuntimeError("claims do Evidence Bundle são incompatíveis")
    return bundle


def verify_evidence_bundle(
    path: Path,
    artifact_path: Path | None = None,
) -> dict[str, Any]:
    bundle = load_evidence_bundle(path)
    if artifact_path is None:
        return bundle
    subject = bundle.get("subject")
    if not isinstance(subject, dict):
        raise MiraiRuntimeError("Evidence Bundle não declara um artefato")
    artifact = artifact_path.expanduser().resolve()
    validate_artifact(artifact)
    digest, size = stable_file_digest(artifact)
    if digest != subject.get("sha256") or size != subject.get("size_bytes"):
        raise MiraiRuntimeError("o artefato não corresponde ao Evidence Bundle")
    return bundle
