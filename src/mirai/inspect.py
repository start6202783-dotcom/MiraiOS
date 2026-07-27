"""Validação e inspeção estrutural de modelos ONNX."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .errors import MiraiRuntimeError


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


def load_onnx_model(model_path: Path) -> tuple[Any, Any]:
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
        model = onnx.load(str(model_path))
    except Exception as error:
        raise MiraiRuntimeError(
            f"falha ao carregar o modelo ONNX: {error}"
        ) from error

    return onnx, model


def validate_model(model_path: Path) -> float:
    """Valida a estrutura ONNX e retorna o tamanho do modelo em kilobytes."""
    onnx, model = load_onnx_model(model_path)

    try:
        onnx.checker.check_model(model)
        return model_path.stat().st_size / BYTES_PER_KB
    except Exception as error:
        raise MiraiRuntimeError(f"modelo ONNX inválido: {error}") from error


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
    onnx, model = load_onnx_model(model_path)
    print(f"[MiraiOS] Informações do modelo: {model_path.name}")
    print_value_info_section("Entradas", model.graph.input, onnx)
    print_value_info_section("Saídas", model.graph.output, onnx)
    print(f"[MiraiOS] Total de nós: {len(model.graph.node)}")
