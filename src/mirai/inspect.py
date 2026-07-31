"""Validação e inspeção estrutural de modelos ONNX."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .errors import MiraiRuntimeError
from .model_guard import ModelSafetyReport, inspect_model_safety
from .package import MAX_MODEL_SIZE_BYTES

ONNX_EXTENSION = ".onnx"
BYTES_PER_KB = 1024


def get_model_path_error(model_path: Path) -> str | None:
    """Retorna o erro do caminho ou ``None`` quando ele pode ser processado."""
    if not model_path.exists():
        return f"arquivo não encontrado: {model_path}"
    if not model_path.is_file():
        return f"o caminho informado não é um arquivo: {model_path}"
    if model_path.suffix.lower() != ONNX_EXTENSION:
        return (
            f"o arquivo deve possuir a extensão {ONNX_EXTENSION}: "
            f"{model_path.name}"
        )
    return None


def ensure_model_path(model_path: Path) -> None:
    """Garante que o caminho aponta para um arquivo com extensão ONNX."""
    if error := get_model_path_error(model_path):
        raise MiraiRuntimeError(error)


def load_onnx_model(model_path: Path) -> tuple[Any, Any, ModelSafetyReport]:
    """Carrega a biblioteca ONNX e o modelo solicitado."""
    ensure_model_path(model_path)

    try:
        import onnx
    except ModuleNotFoundError as error:
        raise MiraiRuntimeError(
            "a dependência 'onnx' não está instalada. "
            "Execute: python -m pip install onnx"
        ) from error

    try:
        size_bytes = model_path.stat().st_size
        if size_bytes <= 0:
            raise MiraiRuntimeError("modelo ONNX vazio")
        if size_bytes > MAX_MODEL_SIZE_BYTES:
            raise MiraiRuntimeError("modelo ONNX excede o limite de 512 MB")
        model = onnx.load(str(model_path), load_external_data=False)
    except Exception as error:
        if isinstance(error, MiraiRuntimeError):
            raise
        raise MiraiRuntimeError(
            f"falha ao carregar o modelo ONNX: {error}"
        ) from error
    report = inspect_model_safety(model, onnx)
    return onnx, model, report


def validate_model(model_path: Path) -> float:
    """Valida a estrutura ONNX e retorna o tamanho do modelo em kilobytes."""
    size_kb, _ = validate_model_with_report(model_path)
    return size_kb


def validate_model_with_report(
    model_path: Path,
) -> tuple[float, ModelSafetyReport]:
    """Valida e devolve a evidência produzida pela quarentena estrutural."""
    onnx, model, report = load_onnx_model(model_path)

    try:
        onnx.checker.check_model(model)
        return model_path.stat().st_size / BYTES_PER_KB, report
    except Exception as error:
        raise MiraiRuntimeError(f"modelo ONNX inválido: {error}") from error


def validate_artifact(artifact_path: Path) -> float:
    """Valida um modelo ONNX ou um pacote .mirai completo."""
    from .package import (
        MIRAI_EXTENSION,
        materialize_model_artifact,
        validate_runtime_contract,
    )
    from .runtime import create_session, load_runtime_dependencies

    if artifact_path.suffix.lower() != MIRAI_EXTENSION:
        return validate_model(artifact_path)
    with materialize_model_artifact(artifact_path) as (model_path, manifest):
        validate_model(model_path)
        ort, _ = load_runtime_dependencies()
        session = create_session(model_path, ort)
        if manifest is None:
            raise MiraiRuntimeError("manifesto ausente no pacote .mirai")
        validate_runtime_contract(
            manifest,
            session.get_inputs(),
            session.get_outputs(),
        )
        del session
    return artifact_path.stat().st_size / BYTES_PER_KB


def describe_value_info(value_info: Any, onnx: Any) -> tuple[str, str]:
    """Formata shape e tipo de uma entrada ou saída ONNX."""
    if not value_info.type.HasField("tensor_type"):
        return "N/A", "NÃO TENSOR"

    tensor_type = value_info.type.tensor_type
    dimensions: list[str] = []

    for dimension in tensor_type.shape.dim:
        dimension_kind = dimension.WhichOneof("value")
        if dimension_kind == "dim_value":
            dimensions.append(str(dimension.dim_value))
        elif dimension_kind == "dim_param":
            dimensions.append(dimension.dim_param or "?")
        else:
            dimensions.append("?")

    try:
        data_type = onnx.TensorProto.DataType.Name(tensor_type.elem_type)
    except (KeyError, ValueError):
        data_type = f"DESCONHECIDO ({tensor_type.elem_type})"

    return f"[{', '.join(dimensions)}]", data_type


def print_value_info_section(
    title: str,
    values: Sequence[Any],
    onnx: Any,
) -> None:
    """Exibe uma seção de entradas ou saídas do modelo."""
    print(f"[MiraiOS] {title}:")
    if not values:
        print("[MiraiOS]   Nenhuma")
        return

    for value_info in values:
        shape, data_type = describe_value_info(value_info, onnx)
        print(f"[MiraiOS]   - Nome: {value_info.name}")
        print(f"[MiraiOS]     Shape: {shape}")
        print(f"[MiraiOS]     Tipo: {data_type}")


def show_model_info(model_path: Path) -> None:
    """Carrega e exibe metadados estruturais de um modelo ONNX."""
    onnx, model, report = load_onnx_model(model_path)
    print(f"[MiraiOS] Informações do modelo: {model_path.name}")
    print_value_info_section("Entradas", model.graph.input, onnx)
    print_value_info_section("Saídas", model.graph.output, onnx)
    print(f"[MiraiOS] Total de nós: {len(model.graph.node)}")
    print(
        "[MiraiOS] Quarentena: "
        f"{report.graph_count} grafo(s), "
        f"{report.initializer_count} initializer(s), "
        "dados externos bloqueados"
    )


def show_artifact_info(artifact_path: Path) -> None:
    """Exibe informações de um ONNX ou do contrato de um pacote .mirai."""
    import tempfile

    from .package import (
        MIRAI_EXTENSION,
        extract_mirai_model,
        load_mirai_package,
        validate_runtime_contract,
    )
    from .runtime import create_session, load_runtime_dependencies

    if artifact_path.suffix.lower() != MIRAI_EXTENSION:
        show_model_info(artifact_path)
        return

    package = load_mirai_package(artifact_path)
    manifest = package.manifest
    with tempfile.TemporaryDirectory(prefix="mirai-info-") as directory:
        model_path = Path(directory) / "model.onnx"
        extract_mirai_model(package, model_path)
        validate_model(model_path)
        ort, _ = load_runtime_dependencies()
        session = create_session(model_path, ort)
        validate_runtime_contract(
            manifest,
            session.get_inputs(),
            session.get_outputs(),
        )
        del session

        _print_package_info(package)
        show_model_info(model_path)


def _print_package_info(package: Any) -> None:
    """Exibe o manifesto de um pacote já validado."""
    manifest = package.manifest
    print(f"[MiraiOS] Pacote: {manifest['name']} v{manifest['version']}")
    print(f"[MiraiOS] Formato: {manifest['format']} v{manifest['format_version']}")
    print(f"[MiraiOS] Runtime: {manifest['runtime']}")
    print(f"[MiraiOS] Artefato SHA-256: {package.sha256}")
    print(f"[MiraiOS] Modelo: {manifest['model']['source_name']}")
    print(f"[MiraiOS] Modelo SHA-256: {manifest['model']['sha256']}")
    description = manifest.get("description")
    if description:
        print(f"[MiraiOS] Descrição: {description}")
    print("[MiraiOS] Contrato de entradas:")
    for item in manifest["inputs"]:
        preprocessing = item["preprocessing"]
        preparation = str(preprocessing["kind"])
        if preprocessing["kind"] == "image":
            preparation = (
                f"image/{preprocessing['layout']} "
                f"scale={preprocessing['scale']}"
            )
        print(
            f"[MiraiOS]   - {item['name']}: {item['type']} "
            f"{item['shape']} ({preparation})"
        )
    print("[MiraiOS] Contrato de saídas:")
    for item in manifest["outputs"]:
        print(
            f"[MiraiOS]   - {item['name']}: "
            f"{item['type']} {item['shape']}"
        )
