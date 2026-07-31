"""Pacotes reproduzíveis de modelos usados pelo MiraiOS."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .errors import MiraiRuntimeError
from .storage import stable_file_digest

MIRAI_EXTENSION = ".mirai"
MIRAI_MEDIA_TYPE = "application/vnd.mirai.package+zip"
MIRAI_FORMAT = "mirai.package"
MIRAI_FORMAT_VERSION = 1
MANIFEST_MEMBER = "manifest.json"
MODEL_MEMBER = "model/model.onnx"
PACKAGE_MEMBERS = {MANIFEST_MEMBER, MODEL_MEMBER}
MAX_MANIFEST_SIZE_BYTES = 64 * 1024
MAX_MODEL_SIZE_BYTES = 512 * 1024 * 1024
MAX_PACKAGE_SIZE_BYTES = (
    MAX_MODEL_SIZE_BYTES + MAX_MANIFEST_SIZE_BYTES + 1024 * 1024
)
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
PACKAGE_NAME_PATTERN = re.compile(
    r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$"
)
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_SOURCE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
IMAGE_INPUT_TYPES = {
    "tensor(double)",
    "tensor(float)",
    "tensor(float16)",
    "tensor(uint8)",
}


@dataclass(frozen=True, slots=True)
class MiraiPackage:
    """Metadados verificados de um arquivo ``.mirai``."""

    path: Path
    manifest: dict[str, Any]
    sha256: str
    size_bytes: int

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    @property
    def version(self) -> str:
        return str(self.manifest["version"])

    @property
    def model_name(self) -> str:
        return str(self.manifest["model"]["source_name"])


def calculate_sha256(path: Path) -> str:
    """Calcula SHA-256 sem carregar o arquivo inteiro em memória."""
    digest, _ = stable_file_digest(path)
    return digest


def ensure_package_path(package_path: Path) -> None:
    """Valida existência, tipo, extensão e limite do pacote."""
    if not package_path.exists():
        raise MiraiRuntimeError(f"arquivo não encontrado: {package_path}")
    if not package_path.is_file():
        raise MiraiRuntimeError(
            f"o caminho informado não é um arquivo: {package_path}"
        )
    if package_path.suffix.lower() != MIRAI_EXTENSION:
        raise MiraiRuntimeError(
            f"o pacote deve possuir a extensão {MIRAI_EXTENSION}: "
            f"{package_path.name}"
        )
    try:
        size_bytes = package_path.stat().st_size
    except OSError as error:
        raise MiraiRuntimeError(
            f"não foi possível inspecionar o pacote: {error}"
        ) from error
    if size_bytes <= 0:
        raise MiraiRuntimeError("pacote .mirai vazio")
    if size_bytes > MAX_PACKAGE_SIZE_BYTES:
        raise MiraiRuntimeError(
            "pacote .mirai excede o limite permitido de aproximadamente 513 MB"
        )


def _reject_unsafe_members(infos: list[zipfile.ZipInfo]) -> None:
    names = [info.filename for info in infos]
    if len(names) != len(set(names)):
        raise MiraiRuntimeError("pacote .mirai contém entradas duplicadas")
    if set(names) != PACKAGE_MEMBERS or len(names) != len(PACKAGE_MEMBERS):
        raise MiraiRuntimeError(
            "pacote .mirai deve conter somente manifest.json e "
            "model/model.onnx"
        )

    for info in infos:
        file_type = stat.S_IFMT(info.external_attr >> 16)
        if (
            info.is_dir()
            or file_type == stat.S_IFLNK
            or file_type not in {0, stat.S_IFREG}
        ):
            raise MiraiRuntimeError(
                f"entrada insegura no pacote .mirai: {info.filename}"
            )
        if info.flag_bits & 0x1:
            raise MiraiRuntimeError("pacotes .mirai criptografados não são aceitos")
        if info.compress_type != zipfile.ZIP_STORED:
            raise MiraiRuntimeError(
                "pacotes .mirai v1 devem usar entradas sem compressão"
            )

    info_by_name = {info.filename: info for info in infos}
    manifest_info = info_by_name[MANIFEST_MEMBER]
    model_info = info_by_name[MODEL_MEMBER]
    if not 0 < manifest_info.file_size <= MAX_MANIFEST_SIZE_BYTES:
        raise MiraiRuntimeError(
            "manifest.json vazio ou acima do limite de 64 KB"
        )
    if not 0 < model_info.file_size <= MAX_MODEL_SIZE_BYTES:
        raise MiraiRuntimeError(
            "modelo do pacote vazio ou acima do limite de 512 MB"
        )


def _require_exact_keys(
    value: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
    label: str,
) -> None:
    optional = optional or set()
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise MiraiRuntimeError(
            f"{label} não contém: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise MiraiRuntimeError(
            f"{label} contém campos desconhecidos: "
            f"{', '.join(sorted(unknown))}"
        )


def _validate_shape(shape: object, label: str) -> list[int | str | None]:
    if not isinstance(shape, list):
        raise MiraiRuntimeError(f"shape de {label} deve ser uma lista")
    normalized: list[int | str | None] = []
    for dimension in shape:
        if dimension is None:
            normalized.append(None)
        elif isinstance(dimension, int) and not isinstance(dimension, bool):
            if dimension <= 0:
                raise MiraiRuntimeError(
                    f"shape de {label} contém dimensão inválida"
                )
            normalized.append(dimension)
        elif isinstance(dimension, str) and 0 < len(dimension) <= 64:
            normalized.append(dimension)
        else:
            raise MiraiRuntimeError(
                f"shape de {label} contém dimensão inválida"
            )
    return normalized


def _validate_numeric_vector(
    value: object,
    *,
    label: str,
    allow_zero: bool,
    positive: bool = False,
) -> list[float]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) not in {1, 3, 4}
    ):
        raise MiraiRuntimeError(
            f"{label} deve conter 1, 3 ou 4 valores numéricos"
        )
    normalized: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise MiraiRuntimeError(f"{label} contém valor não numérico")
        converted = float(item)
        if (
            not math.isfinite(converted)
            or (not allow_zero and converted == 0)
            or (positive and converted <= 0)
        ):
            raise MiraiRuntimeError(f"{label} contém valor inválido")
        normalized.append(converted)
    return normalized


def _validate_preprocessing(
    value: object,
    *,
    input_name: str,
    input_type: str,
    shape: list[int | str | None],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MiraiRuntimeError(
            f"preprocessing da entrada '{input_name}' deve ser um objeto"
        )
    kind = value.get("kind")
    if kind == "tensor":
        _require_exact_keys(
            value,
            required={"kind"},
            label=f"preprocessing de '{input_name}'",
        )
        return {"kind": "tensor"}
    if kind != "image":
        raise MiraiRuntimeError(
            f"preprocessing desconhecido para a entrada '{input_name}'"
        )

    _require_exact_keys(
        value,
        required={"kind", "layout", "resize", "scale", "mean", "std"},
        label=f"preprocessing de '{input_name}'",
    )
    if len(shape) != 4:
        raise MiraiRuntimeError(
            f"entrada de imagem '{input_name}' deve possuir rank 4"
        )
    if input_type not in IMAGE_INPUT_TYPES:
        raise MiraiRuntimeError(
            f"tipo de imagem não suportado em '{input_name}': {input_type}"
        )
    layout = value.get("layout")
    if layout not in {"nchw", "nhwc"}:
        raise MiraiRuntimeError(
            f"layout de '{input_name}' deve ser nchw ou nhwc"
        )
    if value.get("resize") != "stretch":
        raise MiraiRuntimeError(
            f"resize de '{input_name}' deve ser stretch"
        )
    scale = value.get("scale")
    if (
        isinstance(scale, bool)
        or not isinstance(scale, (int, float))
        or not math.isfinite(float(scale))
        or float(scale) <= 0
    ):
        raise MiraiRuntimeError(
            f"scale de '{input_name}' deve ser um número positivo"
        )
    mean = _validate_numeric_vector(
        value.get("mean"),
        label=f"mean de '{input_name}'",
        allow_zero=True,
    )
    std = _validate_numeric_vector(
        value.get("std"),
        label=f"std de '{input_name}'",
        allow_zero=False,
        positive=True,
    )
    if len(mean) != len(std):
        raise MiraiRuntimeError(
            f"mean e std de '{input_name}' devem ter o mesmo tamanho"
        )
    channel_index = 1 if layout == "nchw" else 3
    channels = shape[channel_index]
    if isinstance(channels, int) and len(mean) not in {1, channels}:
        raise MiraiRuntimeError(
            f"normalização de '{input_name}' não corresponde aos canais"
        )
    if (
        "float" not in input_type
        and "double" not in input_type
        and (float(scale) != 1 or any(mean) or any(item != 1 for item in std))
    ):
        raise MiraiRuntimeError(
            f"normalização numérica exige entrada float em '{input_name}'"
        )
    return {
        "kind": "image",
        "layout": layout,
        "resize": "stretch",
        "scale": float(scale),
        "mean": mean,
        "std": std,
    }


def _validate_contract_entries(
    value: object,
    *,
    label: str,
    inputs: bool,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise MiraiRuntimeError(f"{label} deve conter ao menos um item")
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            raise MiraiRuntimeError(f"{item_label} deve ser um objeto")
        required = {"name", "type", "shape"}
        if inputs:
            required.add("preprocessing")
        _require_exact_keys(item, required=required, label=item_label)
        name = item.get("name")
        type_name = item.get("type")
        if not isinstance(name, str) or not 0 < len(name) <= 128:
            raise MiraiRuntimeError(f"nome inválido em {item_label}")
        if name in names:
            raise MiraiRuntimeError(f"nome duplicado em {label}: {name}")
        names.add(name)
        if not isinstance(type_name, str) or not 0 < len(type_name) <= 128:
            raise MiraiRuntimeError(f"tipo inválido em {item_label}")
        shape = _validate_shape(item.get("shape"), item_label)
        normalized_item: dict[str, Any] = {
            "name": name,
            "type": type_name,
            "shape": shape,
        }
        if inputs:
            normalized_item["preprocessing"] = _validate_preprocessing(
                item.get("preprocessing"),
                input_name=name,
                input_type=type_name,
                shape=shape,
            )
        normalized.append(normalized_item)
    return normalized


def validate_manifest(
    manifest: object,
    *,
    model_size_bytes: int,
) -> dict[str, Any]:
    """Valida estritamente o manifesto público do formato v1."""
    if not isinstance(manifest, dict):
        raise MiraiRuntimeError("manifest.json deve conter um objeto JSON")
    _require_exact_keys(
        manifest,
        required={
            "format",
            "format_version",
            "name",
            "version",
            "description",
            "runtime",
            "model",
            "inputs",
            "outputs",
            "created_by",
        },
        label="manifest.json",
    )
    if manifest.get("format") != MIRAI_FORMAT:
        raise MiraiRuntimeError("formato do pacote .mirai não reconhecido")
    format_version = manifest.get("format_version")
    if (
        isinstance(format_version, bool)
        or not isinstance(format_version, int)
        or format_version != MIRAI_FORMAT_VERSION
    ):
        raise MiraiRuntimeError(
            "versão do formato .mirai não suportada por esta CLI"
        )
    name = manifest.get("name")
    version = manifest.get("version")
    description = manifest.get("description")
    if not isinstance(name, str) or not PACKAGE_NAME_PATTERN.fullmatch(name):
        raise MiraiRuntimeError(
            "nome do pacote inválido; use letras minúsculas, números, "
            "ponto, hífen ou sublinhado"
        )
    if not isinstance(version, str) or not SEMVER_PATTERN.fullmatch(version):
        raise MiraiRuntimeError(
            "versão do pacote deve seguir SemVer (ex.: 1.0.0)"
        )
    if description is not None and (
        not isinstance(description, str) or not 0 < len(description) <= 512
    ):
        raise MiraiRuntimeError(
            "descrição do pacote deve possuir de 1 a 512 caracteres"
        )
    if manifest.get("runtime") != "onnxruntime":
        raise MiraiRuntimeError("runtime do pacote deve ser onnxruntime")

    model = manifest.get("model")
    if not isinstance(model, dict):
        raise MiraiRuntimeError("model no manifesto deve ser um objeto")
    _require_exact_keys(
        model,
        required={"path", "source_name", "sha256", "size_bytes"},
        label="model",
    )
    source_name = model.get("source_name")
    declared_model_size = model.get("size_bytes")
    if (
        model.get("path") != MODEL_MEMBER
        or not isinstance(source_name, str)
        or not SAFE_SOURCE_NAME_PATTERN.fullmatch(source_name)
        or Path(source_name).suffix.lower() != ".onnx"
        or not isinstance(model.get("sha256"), str)
        or not SHA256_PATTERN.fullmatch(model["sha256"])
        or isinstance(declared_model_size, bool)
        or not isinstance(declared_model_size, int)
        or declared_model_size != model_size_bytes
    ):
        raise MiraiRuntimeError("metadados do modelo no manifesto são inválidos")

    inputs = _validate_contract_entries(
        manifest.get("inputs"),
        label="inputs",
        inputs=True,
    )
    outputs = _validate_contract_entries(
        manifest.get("outputs"),
        label="outputs",
        inputs=False,
    )
    created_by = manifest.get("created_by")
    if not isinstance(created_by, dict):
        raise MiraiRuntimeError("created_by deve ser um objeto")
    _require_exact_keys(
        created_by,
        required={"tool", "version"},
        label="created_by",
    )
    if (
        created_by.get("tool") != "miraios"
        or not isinstance(created_by.get("version"), str)
        or not SEMVER_PATTERN.fullmatch(created_by["version"])
    ):
        raise MiraiRuntimeError("created_by possui formato inválido")

    return {
        "format": MIRAI_FORMAT,
        "format_version": MIRAI_FORMAT_VERSION,
        "name": name,
        "version": version,
        "description": description,
        "runtime": "onnxruntime",
        "model": {
            "path": MODEL_MEMBER,
            "source_name": source_name,
            "sha256": model["sha256"],
            "size_bytes": model_size_bytes,
        },
        "inputs": inputs,
        "outputs": outputs,
        "created_by": {
            "tool": "miraios",
            "version": created_by["version"],
        },
    }


def validate_package_metadata(
    name: str,
    version: str,
    description: str | None = None,
) -> None:
    """Valida a identidade antes de usá-la em nomes de arquivo."""
    if not isinstance(name, str) or not PACKAGE_NAME_PATTERN.fullmatch(name):
        raise MiraiRuntimeError(
            "nome do pacote inválido; use letras minúsculas, números, "
            "ponto, hífen ou sublinhado"
        )
    if not isinstance(version, str) or not SEMVER_PATTERN.fullmatch(version):
        raise MiraiRuntimeError(
            "versão do pacote deve seguir SemVer (ex.: 1.0.0)"
        )
    if description is not None and (
        not isinstance(description, str)
        or not 0 < len(description) <= 512
    ):
        raise MiraiRuntimeError(
            "descrição do pacote deve possuir de 1 a 512 caracteres"
        )


def _safe_source_model_name(model_path: Path) -> str:
    """Preserva um nome legível sem inserir caminhos no manifesto."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", model_path.stem).strip(".-_")
    if not stem:
        stem = "model"
    return f"{stem[:122]}.onnx"


def _hash_archive_member(
    archive: zipfile.ZipFile,
    member: str,
) -> str:
    digest = hashlib.sha256()
    with archive.open(member, "r") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MiraiRuntimeError(
                f"manifest.json contém campo duplicado: {key}"
            )
        result[key] = value
    return result


def load_mirai_package(package_path: Path) -> MiraiPackage:
    """Abre, valida e verifica a integridade interna de um pacote."""
    ensure_package_path(package_path)
    try:
        with zipfile.ZipFile(package_path, "r") as archive:
            infos = archive.infolist()
            _reject_unsafe_members(infos)
            model_size = archive.getinfo(MODEL_MEMBER).file_size
            try:
                manifest_raw = archive.read(MANIFEST_MEMBER)
            except (KeyError, RuntimeError, OSError) as error:
                raise MiraiRuntimeError(
                    f"não foi possível ler manifest.json: {error}"
                ) from error
            try:
                manifest_data = json.loads(
                    manifest_raw.decode("utf-8"),
                    object_pairs_hook=_json_object_without_duplicates,
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise MiraiRuntimeError(
                    f"manifest.json inválido: {error}"
                ) from error
            manifest = validate_manifest(
                manifest_data,
                model_size_bytes=model_size,
            )
            actual_model_hash = _hash_archive_member(archive, MODEL_MEMBER)
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        KeyError,
        NotImplementedError,
        RuntimeError,
        OSError,
    ) as error:
        raise MiraiRuntimeError(f"pacote .mirai inválido: {error}") from error

    if actual_model_hash != manifest["model"]["sha256"]:
        raise MiraiRuntimeError(
            "SHA-256 do modelo não corresponde ao manifest.json"
        )
    return MiraiPackage(
        path=package_path,
        manifest=manifest,
        sha256=calculate_sha256(package_path),
        size_bytes=package_path.stat().st_size,
    )


def extract_mirai_model(package: MiraiPackage, target_path: Path) -> Path:
    """Extrai somente o modelo já verificado para um caminho controlado."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(package.path, "r") as archive:
            with archive.open(MODEL_MEMBER, "r") as source:
                with target_path.open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        target_path.unlink(missing_ok=True)
        raise MiraiRuntimeError(
            f"não foi possível extrair o modelo do pacote: {error}"
        ) from error
    actual_hash = calculate_sha256(target_path)
    if actual_hash != package.manifest["model"]["sha256"]:
        target_path.unlink(missing_ok=True)
        raise MiraiRuntimeError(
            "modelo extraído não corresponde ao manifest.json"
        )
    return target_path


def preprocessing_from_manifest(
    manifest: dict[str, Any] | None,
) -> dict[str, dict[str, Any]] | None:
    """Indexa os perfis de preparação declarados no manifesto."""
    if manifest is None:
        return None
    return {
        str(item["name"]): dict(item["preprocessing"])
        for item in manifest["inputs"]
    }


@contextmanager
def materialize_model_artifact(
    artifact_path: Path,
) -> Iterator[tuple[Path, dict[str, Any] | None]]:
    """Resolve um ONNX direto ou extrai temporariamente um pacote .mirai."""
    if artifact_path.suffix.lower() == ".onnx":
        from .inspect import ensure_model_path

        ensure_model_path(artifact_path)
        yield artifact_path, None
        return
    if artifact_path.suffix.lower() != MIRAI_EXTENSION:
        raise MiraiRuntimeError(
            "o artefato deve possuir extensão .onnx ou .mirai"
        )

    package = load_mirai_package(artifact_path)
    with tempfile.TemporaryDirectory(prefix="mirai-package-") as directory:
        model_path = Path(directory) / "model.onnx"
        extract_mirai_model(package, model_path)
        yield model_path, package.manifest


def _normalize_shape(shape: list[Any]) -> list[int | str | None]:
    return [
        dimension
        if isinstance(dimension, (int, str)) and not isinstance(dimension, bool)
        else None
        for dimension in shape
    ]


def _contract_entry(meta: Any) -> dict[str, Any]:
    return {
        "name": str(meta.name),
        "type": str(meta.type),
        "shape": _normalize_shape(list(meta.shape)),
    }


def validate_runtime_contract(
    manifest: dict[str, Any],
    model_inputs: list[Any],
    model_outputs: list[Any],
) -> None:
    """Confirma que o contrato declarado corresponde ao modelo real."""
    actual_inputs = [_contract_entry(meta) for meta in model_inputs]
    expected_inputs = [
        {
            "name": item["name"],
            "type": item["type"],
            "shape": item["shape"],
        }
        for item in manifest["inputs"]
    ]
    actual_outputs = [_contract_entry(meta) for meta in model_outputs]
    expected_outputs = [
        {
            "name": item["name"],
            "type": item["type"],
            "shape": item["shape"],
        }
        for item in manifest["outputs"]
    ]
    if actual_inputs != expected_inputs or actual_outputs != expected_outputs:
        raise MiraiRuntimeError(
            "contrato de entradas e saídas não corresponde ao modelo ONNX"
        )


def _parse_vector_option(
    value: str | None,
    *,
    label: str,
) -> list[float] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise MiraiRuntimeError(
            f"{label} deve ser um array JSON (ex.: [0.5, 0.5, 0.5])"
        ) from error
    return _validate_numeric_vector(
        parsed,
        label=label,
        allow_zero=label == "mean",
        positive=label == "std",
    )


def _image_preprocessing(
    *,
    input_meta: Any,
    requested_layout: str,
    scale_option: float | None,
    mean_option: str | None,
    std_option: str | None,
) -> dict[str, Any]:
    from .inputs import detect_image_layout

    shape = _normalize_shape(list(input_meta.shape))
    layout = detect_image_layout(shape, requested_layout)
    channel_index = 1 if layout == "nchw" else 3
    raw_channels = shape[channel_index]
    channels = raw_channels if isinstance(raw_channels, int) else 3
    if channels not in {1, 3, 4}:
        raise MiraiRuntimeError(
            f"entrada '{input_meta.name}' possui canais incompatíveis "
            "com imagem"
        )
    mean = _parse_vector_option(mean_option, label="mean")
    std = _parse_vector_option(std_option, label="std")
    if mean is None:
        mean = [0.0] * channels
    if std is None:
        std = [1.0] * channels
    if len(mean) not in {1, channels} or len(std) not in {1, channels}:
        raise MiraiRuntimeError(
            "mean e std devem possuir um valor ou um valor por canal"
        )
    if input_meta.type not in IMAGE_INPUT_TYPES:
        raise MiraiRuntimeError(
            f"tipo de imagem não suportado em '{input_meta.name}': "
            f"{input_meta.type}"
        )
    is_float = "float" in input_meta.type or "double" in input_meta.type
    scale = scale_option
    if scale is None:
        scale = 1.0 / 255.0 if is_float else 1.0
    if (
        isinstance(scale, bool)
        or not isinstance(scale, (int, float))
        or not math.isfinite(float(scale))
        or float(scale) <= 0
    ):
        raise MiraiRuntimeError("scale deve ser um número positivo")
    if not is_float and (
        float(scale) != 1
        or any(item != 0 for item in mean)
        or any(item != 1 for item in std)
    ):
        raise MiraiRuntimeError(
            "mean e std personalizados exigem uma entrada de imagem float"
        )
    return {
        "kind": "image",
        "layout": layout,
        "resize": "stretch",
        "scale": float(scale),
        "mean": mean,
        "std": std,
    }


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def create_mirai_package(
    model_path: Path,
    output_path: Path,
    *,
    name: str,
    package_version: str,
    description: str | None = None,
    image_input: str | None = None,
    layout: str = "auto",
    scale: float | None = None,
    mean: str | None = None,
    std: str | None = None,
    replace: bool = False,
) -> MiraiPackage:
    """Cria um pacote determinístico após validar modelo e contrato."""
    from .inspect import validate_model
    from .runtime import create_session, load_runtime_dependencies

    validate_package_metadata(name, package_version, description)
    try:
        model_size = model_path.stat().st_size
    except OSError:
        model_size = 0
    if model_size > MAX_MODEL_SIZE_BYTES:
        raise MiraiRuntimeError("modelo excede o limite de 512 MB")
    validate_model(model_path)
    if output_path.suffix.lower() != MIRAI_EXTENSION:
        raise MiraiRuntimeError("o arquivo de saída deve usar a extensão .mirai")
    if output_path.exists() and not replace:
        raise MiraiRuntimeError(
            f"arquivo de saída já existe: {output_path}; use --replace"
        )

    ort, _ = load_runtime_dependencies()
    session = create_session(model_path, ort)
    inputs = [_contract_entry(meta) for meta in session.get_inputs()]
    outputs = [_contract_entry(meta) for meta in session.get_outputs()]
    input_names = {item["name"] for item in inputs}
    if image_input is not None and image_input not in input_names:
        raise MiraiRuntimeError(
            f"entrada de imagem desconhecida: {image_input}; disponíveis: "
            f"{', '.join(sorted(input_names))}"
        )
    if image_input is None and (
        layout != "auto"
        or scale is not None
        or mean is not None
        or std is not None
    ):
        raise MiraiRuntimeError(
            "--layout, --scale, --mean e --std exigem --image-input"
        )

    runtime_inputs = {meta.name: meta for meta in session.get_inputs()}
    for item in inputs:
        if item["name"] == image_input:
            item["preprocessing"] = _image_preprocessing(
                input_meta=runtime_inputs[item["name"]],
                requested_layout=layout,
                scale_option=scale,
                mean_option=mean,
                std_option=std,
            )
        else:
            item["preprocessing"] = {"kind": "tensor"}
    del session

    manifest_data: dict[str, Any] = {
        "format": MIRAI_FORMAT,
        "format_version": MIRAI_FORMAT_VERSION,
        "name": name,
        "version": package_version,
        "description": description,
        "runtime": "onnxruntime",
        "model": {
            "path": MODEL_MEMBER,
            "source_name": _safe_source_model_name(model_path),
            "sha256": calculate_sha256(model_path),
            "size_bytes": model_size,
        },
        "inputs": inputs,
        "outputs": outputs,
        "created_by": {
            "tool": "miraios",
            "version": __version__,
        },
    }
    manifest = validate_manifest(
        manifest_data,
        model_size_bytes=model_size,
    )
    encoded_manifest = (
        json.dumps(
            manifest,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=MIRAI_EXTENSION,
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
    except OSError as error:
        raise MiraiRuntimeError(
            f"não foi possível preparar o pacote .mirai: {error}"
        ) from error
    try:
        with zipfile.ZipFile(
            temporary_path,
            "w",
            compression=zipfile.ZIP_STORED,
            strict_timestamps=True,
        ) as archive:
            archive.writestr(_zip_info(MANIFEST_MEMBER), encoded_manifest)
            with model_path.open("rb") as model_file:
                with archive.open(_zip_info(MODEL_MEMBER), "w") as member:
                    shutil.copyfileobj(
                        model_file,
                        member,
                        length=1024 * 1024,
                    )
        verified_package = load_mirai_package(temporary_path)
        if output_path.exists() and not replace:
            raise MiraiRuntimeError(
                f"arquivo de saída já existe: {output_path}; use --replace"
            )
        temporary_path.replace(output_path)
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        temporary_path.unlink(missing_ok=True)
        raise MiraiRuntimeError(
            f"não foi possível criar o pacote .mirai: {error}"
        ) from error
    return MiraiPackage(
        path=output_path,
        manifest=verified_package.manifest,
        sha256=verified_package.sha256,
        size_bytes=verified_package.size_bytes,
    )
