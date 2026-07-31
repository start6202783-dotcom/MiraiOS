"""Quarentena estrutural para modelos ONNX antes de criar uma sessão."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .errors import MiraiRuntimeError

MAX_GRAPH_DEPTH = 16
MAX_GRAPH_NODES = 100_000
MAX_STATIC_TENSOR_ELEMENTS = 100_000_000
MAX_TENSOR_RANK = 16
MAX_INITIALIZER_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ModelSafetyReport:
    """Resumo verificável do que atravessou a quarentena estrutural."""

    graph_count: int
    node_count: int
    initializer_count: int
    initializer_bytes: int
    maximum_graph_depth: int
    maximum_tensor_rank: int
    external_data: bool

    def as_dict(self) -> dict[str, int | bool]:
        return asdict(self)


def _element_count(dimensions: list[int], label: str) -> int:
    count = 1
    for dimension in dimensions:
        if dimension < 0:
            raise MiraiRuntimeError(f"{label} contém dimensão negativa")
        count *= dimension
        if count > MAX_STATIC_TENSOR_ELEMENTS:
            raise MiraiRuntimeError(
                f"{label} excede {MAX_STATIC_TENSOR_ELEMENTS:,} elementos estáticos"
            )
    return count


def _check_value_info(value_info: Any, label: str) -> int:
    if not value_info.type.HasField("tensor_type"):
        return 0
    dimensions = list(value_info.type.tensor_type.shape.dim)
    if len(dimensions) > MAX_TENSOR_RANK:
        raise MiraiRuntimeError(
            f"{label} excede o rank máximo de {MAX_TENSOR_RANK}"
        )
    fixed = [
        int(dimension.dim_value)
        for dimension in dimensions
        if dimension.WhichOneof("value") == "dim_value"
    ]
    if len(fixed) == len(dimensions):
        _element_count(fixed, label)
    return len(dimensions)


def inspect_model_safety(model: Any, onnx: Any) -> ModelSafetyReport:
    """Recusa referências externas e estruturas capazes de ampliar recursos."""
    graphs: list[tuple[Any, int]] = [(model.graph, 1)]
    graph_count = 0
    node_count = 0
    initializer_count = 0
    initializer_bytes = 0
    maximum_depth = 0
    maximum_rank = 0
    external_data = False

    while graphs:
        graph, depth = graphs.pop()
        if depth > MAX_GRAPH_DEPTH:
            raise MiraiRuntimeError(
                f"modelo excede a profundidade máxima de {MAX_GRAPH_DEPTH} grafos"
            )
        graph_count += 1
        maximum_depth = max(maximum_depth, depth)
        node_count += len(graph.node)
        if node_count > MAX_GRAPH_NODES:
            raise MiraiRuntimeError(
                f"modelo excede o limite de {MAX_GRAPH_NODES:,} nós"
            )

        for value_info in (*graph.input, *graph.output, *graph.value_info):
            maximum_rank = max(
                maximum_rank,
                _check_value_info(value_info, f"tensor '{value_info.name}'"),
            )

        tensors = list(graph.initializer)
        tensors.extend(item.values for item in graph.sparse_initializer)
        initializer_count += len(tensors)
        for tensor in tensors:
            rank = len(tensor.dims)
            if rank > MAX_TENSOR_RANK:
                raise MiraiRuntimeError(
                    f"initializer '{tensor.name}' excede o rank máximo"
                )
            maximum_rank = max(maximum_rank, rank)
            _element_count(
                [int(dimension) for dimension in tensor.dims],
                f"initializer '{tensor.name}'",
            )
            if onnx.external_data_helper.uses_external_data(tensor):
                external_data = True
            initializer_bytes += int(tensor.ByteSize())
            if initializer_bytes > MAX_INITIALIZER_BYTES:
                raise MiraiRuntimeError(
                    "initializers excedem o limite agregado de 512 MB"
                )

        for node in graph.node:
            for attribute in node.attribute:
                if attribute.type == onnx.AttributeProto.GRAPH:
                    graphs.append((attribute.g, depth + 1))
                elif attribute.type == onnx.AttributeProto.GRAPHS:
                    graphs.extend((item, depth + 1) for item in attribute.graphs)
                elif attribute.type == onnx.AttributeProto.TENSOR:
                    if onnx.external_data_helper.uses_external_data(attribute.t):
                        external_data = True
                elif attribute.type == onnx.AttributeProto.TENSORS and any(
                    onnx.external_data_helper.uses_external_data(item)
                    for item in attribute.tensors
                ):
                    external_data = True

    if external_data:
        raise MiraiRuntimeError(
            "modelos ONNX com dados externos não são aceitos; "
            "empacote todos os pesos no próprio arquivo"
        )
    return ModelSafetyReport(
        graph_count=graph_count,
        node_count=node_count,
        initializer_count=initializer_count,
        initializer_bytes=initializer_bytes,
        maximum_graph_depth=maximum_depth,
        maximum_tensor_rank=maximum_rank,
        external_data=False,
    )
