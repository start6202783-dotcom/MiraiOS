"""Passaporte reproduzível que descreve identidade, contrato e limites do modelo."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import __version__
from .errors import MiraiRuntimeError
from .inspect import load_onnx_model, validate_artifact
from .json_codec import strict_json_dumps, strict_json_loads
from .package import (
    MIRAI_EXTENSION,
    calculate_sha256,
    load_mirai_package,
    materialize_model_artifact,
)
from .runtime import create_session, load_runtime_dependencies
from .storage import atomic_write_text

MODEL_PASSPORT_FORMAT = "mirai.model-passport"
MODEL_PASSPORT_VERSION = 1
MAX_PASSPORT_SIZE_BYTES = 512 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_TEXT_CHARS = 4096
MAX_LIMITATIONS = 32


def _optional_text(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        raise MiraiRuntimeError(f"{label} não pode ser vazio")
    if len(cleaned) > MAX_TEXT_CHARS:
        raise MiraiRuntimeError(
            f"{label} excede o limite de {MAX_TEXT_CHARS} caracteres"
        )
    return cleaned


def _shape(value: object) -> list[int | str | None]:
    if not isinstance(value, (list, tuple)):
        return []
    return [
        item
        if isinstance(item, (int, str)) and not isinstance(item, bool)
        else None
        for item in value
    ]


def _contract_entry(meta: Any) -> dict[str, Any]:
    return {
        "name": str(meta.name),
        "type": str(meta.type),
        "shape": _shape(meta.shape),
    }


def _precision(model: Any, onnx: Any) -> str:
    names: set[str] = set()
    for initializer in model.graph.initializer:
        try:
            names.add(str(onnx.TensorProto.DataType.Name(initializer.data_type)).lower())
        except (AttributeError, ValueError):
            names.add(str(initializer.data_type))
    if not names:
        return "unknown"
    normalized: set[str] = set()
    for name in names:
        if name in {"int8", "uint8"}:
            normalized.add("int8")
        elif name == "float16":
            normalized.add("fp16")
        elif name == "bfloat16":
            normalized.add("bf16")
        elif name == "float":
            normalized.add("fp32")
        elif name == "double":
            normalized.add("fp64")
        else:
            normalized.add(name)
    return next(iter(normalized)) if len(normalized) == 1 else "mixed"


def _model_details(model_path: Path) -> dict[str, Any]:
    onnx, model, safety = load_onnx_model(model_path)
    opsets = [
        {
            "domain": str(item.domain or "ai.onnx"),
            "version": int(item.version),
        }
        for item in model.opset_import
    ]
    opsets.sort(key=lambda item: (item["domain"], item["version"]))
    safety_details = {
        "graphs": safety.graph_count,
        "nodes": safety.node_count,
        "initializers": safety.initializer_count,
        "initializer_bytes": safety.initializer_bytes,
        "max_subgraph_depth": safety.maximum_graph_depth,
        "max_tensor_rank": safety.maximum_tensor_rank,
        "external_data": safety.external_data,
    }
    return {
        "format": "onnx",
        "precision": _precision(model, onnx),
        "producer_name": str(model.producer_name or "unknown")[:MAX_TEXT_CHARS],
        "producer_version": str(model.producer_version or "unknown")[:MAX_TEXT_CHARS],
        "opsets": opsets,
        "safety": safety_details,
    }


def create_model_passport(
    artifact_path: Path,
    output_path: Path,
    *,
    author: str | None = None,
    license_expression: str | None = None,
    source_uri: str | None = None,
    intended_use: str | None = None,
    limitations: list[str] | tuple[str, ...] | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Gera um documento determinístico sem alegar origem não informada."""
    artifact = artifact_path.expanduser().resolve()
    output = output_path.expanduser().resolve()
    if output.suffix.lower() != ".json":
        raise MiraiRuntimeError("o Model Passport deve usar a extensão .json")
    if output.exists() and not replace:
        raise MiraiRuntimeError(f"arquivo de saída já existe: {output}")
    validated_limitations: list[str] = []
    for item in limitations or []:
        validated = _optional_text(item, "limitação")
        if validated is not None:
            validated_limitations.append(validated)
    if len(validated_limitations) > MAX_LIMITATIONS:
        raise MiraiRuntimeError(
            f"o passaporte aceita no máximo {MAX_LIMITATIONS} limitações"
        )

    validate_artifact(artifact)
    artifact_sha = calculate_sha256(artifact)
    artifact_size = artifact.stat().st_size
    package_manifest: dict[str, Any] | None = None
    package_details: dict[str, Any] | None = None
    if artifact.suffix.lower() == MIRAI_EXTENSION:
        package = load_mirai_package(artifact)
        package_manifest = package.manifest
        package_details = {
            "name": package.name,
            "version": package.version,
            "format_version": int(package.manifest["format_version"]),
            "description": package.manifest.get("description"),
            "sha256": package.sha256,
        }

    with materialize_model_artifact(artifact) as (model_path, manifest):
        model_details = _model_details(model_path)
        ort, _ = load_runtime_dependencies()
        session = create_session(model_path, ort, "cpu")
        contract = {
            "inputs": (
                [dict(item) for item in manifest["inputs"]]
                if manifest is not None
                else [_contract_entry(item) for item in session.get_inputs()]
            ),
            "outputs": (
                [dict(item) for item in manifest["outputs"]]
                if manifest is not None
                else [_contract_entry(item) for item in session.get_outputs()]
            ),
        }
        model_sha = (
            str(package_manifest["model"]["sha256"])
            if package_manifest is not None
            else calculate_sha256(model_path)
        )
        model_size = (
            int(package_manifest["model"]["size_bytes"])
            if package_manifest is not None
            else model_path.stat().st_size
        )
        source_name = (
            str(package_manifest["model"]["source_name"])
            if package_manifest is not None
            else artifact.name
        )
        del session

    passport: dict[str, Any] = {
        "format": MODEL_PASSPORT_FORMAT,
        "schema_version": MODEL_PASSPORT_VERSION,
        "subject": {
            "name": artifact.name,
            "kind": "mirai-package" if package_details else "onnx-model",
            "sha256": artifact_sha,
            "size_bytes": artifact_size,
        },
        "model": {
            **model_details,
            "source_name": source_name,
            "sha256": model_sha,
            "size_bytes": model_size,
        },
        "package": package_details,
        "contract": contract,
        "runtime": {
            "name": "onnxruntime",
            "version": str(getattr(ort, "__version__", "unknown")),
        },
        "provenance": {
            "author": _optional_text(author, "autor"),
            "license": _optional_text(license_expression, "licença"),
            "source_uri": _optional_text(source_uri, "source URI"),
            "claims_are_user_supplied": any(
                value is not None for value in (author, license_expression, source_uri)
            ),
        },
        "intended_use": _optional_text(intended_use, "uso pretendido"),
        "limitations": validated_limitations,
        "compatibility": {
            "status": "unverified",
            "evidence": [],
        },
        "created_by": {
            "tool": "miraios",
            "version": __version__,
        },
    }
    try:
        atomic_write_text(
            output,
            strict_json_dumps(passport, pretty=True, sort_keys=True) + "\n",
            mode=0o600,
        )
    except OSError as error:
        raise MiraiRuntimeError(
            f"não foi possível gravar o Model Passport: {error}"
        ) from error
    return passport


def load_model_passport(path: Path) -> dict[str, Any]:
    target = path.expanduser().resolve()
    try:
        size = target.stat().st_size
        content = target.read_bytes()
    except OSError as error:
        raise MiraiRuntimeError(f"não foi possível ler o Model Passport: {error}") from error
    if not 0 < size <= MAX_PASSPORT_SIZE_BYTES:
        raise MiraiRuntimeError("Model Passport vazio ou acima de 512 KB")
    payload = strict_json_loads(content, label="Model Passport")
    if not isinstance(payload, dict):
        raise MiraiRuntimeError("Model Passport deve ser um objeto JSON")
    required = {
        "format",
        "schema_version",
        "subject",
        "model",
        "package",
        "contract",
        "runtime",
        "provenance",
        "intended_use",
        "limitations",
        "compatibility",
        "created_by",
    }
    if set(payload) != required:
        raise MiraiRuntimeError("Model Passport possui campos incompatíveis")
    if (
        payload["format"] != MODEL_PASSPORT_FORMAT
        or payload["schema_version"] != MODEL_PASSPORT_VERSION
    ):
        raise MiraiRuntimeError("versão do Model Passport não suportada")
    subject = payload.get("subject")
    if not isinstance(subject, dict) or set(subject) != {
        "name",
        "kind",
        "sha256",
        "size_bytes",
    }:
        raise MiraiRuntimeError("subject do Model Passport inválido")
    if not isinstance(subject.get("sha256"), str) or not SHA256_PATTERN.fullmatch(
        subject["sha256"]
    ):
        raise MiraiRuntimeError("SHA-256 do Model Passport inválido")
    if (
        isinstance(subject.get("size_bytes"), bool)
        or not isinstance(subject.get("size_bytes"), int)
        or subject["size_bytes"] <= 0
    ):
        raise MiraiRuntimeError("tamanho do subject no Model Passport inválido")
    if (
        not isinstance(subject.get("name"), str)
        or not subject["name"]
        or Path(subject["name"]).name != subject["name"]
        or subject.get("kind") not in {"onnx-model", "mirai-package"}
    ):
        raise MiraiRuntimeError("identidade do subject no Model Passport inválida")
    model = payload.get("model")
    if (
        not isinstance(model, dict)
        or model.get("format") != "onnx"
        or not isinstance(model.get("sha256"), str)
        or not SHA256_PATTERN.fullmatch(model["sha256"])
        or isinstance(model.get("size_bytes"), bool)
        or not isinstance(model.get("size_bytes"), int)
        or model["size_bytes"] <= 0
        or not isinstance(model.get("opsets"), list)
        or not isinstance(model.get("safety"), dict)
    ):
        raise MiraiRuntimeError("descrição do modelo no Model Passport inválida")
    package = payload.get("package")
    if subject["kind"] == "mirai-package" and not isinstance(package, dict):
        raise MiraiRuntimeError("metadados do pacote ausentes no Model Passport")
    if subject["kind"] == "onnx-model" and package is not None:
        raise MiraiRuntimeError("metadados de pacote inesperados no Model Passport")
    contract = payload.get("contract")
    if (
        not isinstance(contract, dict)
        or set(contract) != {"inputs", "outputs"}
        or not isinstance(contract["inputs"], list)
        or not isinstance(contract["outputs"], list)
    ):
        raise MiraiRuntimeError("contrato do Model Passport inválido")
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict) or set(provenance) != {
        "author",
        "license",
        "source_uri",
        "claims_are_user_supplied",
    }:
        raise MiraiRuntimeError("proveniência do Model Passport inválida")
    limitations = payload.get("limitations")
    if (
        not isinstance(limitations, list)
        or len(limitations) > MAX_LIMITATIONS
        or any(not isinstance(item, str) or not item for item in limitations)
    ):
        raise MiraiRuntimeError("limitações do Model Passport inválidas")
    return payload


def verify_model_passport(path: Path, artifact_path: Path) -> dict[str, Any]:
    passport = load_model_passport(path)
    artifact = artifact_path.expanduser().resolve()
    validate_artifact(artifact)
    subject = passport["subject"]
    if subject["name"] != artifact.name:
        raise MiraiRuntimeError("o nome do artefato não corresponde ao passaporte")
    if subject["size_bytes"] != artifact.stat().st_size:
        raise MiraiRuntimeError("o tamanho do artefato não corresponde ao passaporte")
    if subject["sha256"] != calculate_sha256(artifact):
        raise MiraiRuntimeError("o SHA-256 do artefato não corresponde ao passaporte")
    return passport
