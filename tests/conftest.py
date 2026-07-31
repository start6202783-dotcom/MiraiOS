"""Fixtures ONNX usadas pela suíte do MiraiOS."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper
from PIL import Image

from mirai.package import create_mirai_package

TEST_MODEL_IR_VERSION = 10


def save_runtime_compatible_model(
    model: onnx.ModelProto,
    path: Path,
) -> Path:
    """Salva uma fixture compatível com toda a matriz do ONNX Runtime."""
    model.ir_version = TEST_MODEL_IR_VERSION
    onnx.checker.check_model(model)
    onnx.save(model, path)
    return path


@pytest.fixture
def model_factory(
    tmp_path: Path,
) -> Callable[[str, list[int | str | None], int], Path]:
    """Cria modelos Identity com shape e dtype configuráveis."""

    def factory(
        name: str,
        shape: list[int | str | None],
        dtype: int = TensorProto.FLOAT,
    ) -> Path:
        model_input = helper.make_tensor_value_info("input", dtype, shape)
        model_output = helper.make_tensor_value_info("output", dtype, shape)
        node = helper.make_node("Identity", ["input"], ["output"])
        graph = helper.make_graph(
            [node],
            f"{name}Graph",
            [model_input],
            [model_output],
        )
        model = helper.make_model(
            graph,
            producer_name="MiraiOSTests",
            opset_imports=[helper.make_opsetid("", 13)],
        )
        path = tmp_path / f"{name}.onnx"
        return save_runtime_compatible_model(model, path)

    return factory


@pytest.fixture
def dummy_model(tmp_path: Path) -> Path:
    """Cria um modelo que soma 1.0 a uma entrada float de shape [1]."""
    model_input = helper.make_tensor_value_info(
        "input",
        TensorProto.FLOAT,
        [1],
    )
    model_output = helper.make_tensor_value_info(
        "output",
        TensorProto.FLOAT,
        [1],
    )
    constant = numpy_helper.from_array(
        np.array([1.0], dtype=np.float32),
        name="constant",
    )
    node = helper.make_node(
        "Add",
        ["input", "constant"],
        ["output"],
    )
    graph = helper.make_graph(
        [node],
        "DummyGraph",
        [model_input],
        [model_output],
        [constant],
    )
    model = helper.make_model(
        graph,
        producer_name="MiraiOSTests",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    path = tmp_path / "dummy.onnx"
    return save_runtime_compatible_model(model, path)


@pytest.fixture
def dummy_package(dummy_model: Path, tmp_path: Path) -> Path:
    """Empacota o modelo simples no formato público .mirai v1."""
    path = tmp_path / "dummy-1.0.0.mirai"
    create_mirai_package(
        dummy_model,
        path,
        name="dummy",
        package_version="1.0.0",
        description="Modelo determinístico de teste",
    )
    return path


@pytest.fixture
def multi_input_model(tmp_path: Path) -> Path:
    """Cria um modelo Add com entradas nomeadas x e y."""
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1])
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [1])
    output = helper.make_tensor_value_info("sum", TensorProto.FLOAT, [1])
    node = helper.make_node("Add", ["x", "y"], ["sum"])
    graph = helper.make_graph([node], "MultiInputGraph", [x, y], [output])
    model = helper.make_model(
        graph,
        producer_name="MiraiOSTests",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    path = tmp_path / "multi-input.onnx"
    return save_runtime_compatible_model(model, path)


@pytest.fixture
def sample_image(tmp_path: Path) -> Path:
    """Cria uma imagem RGB pequena e determinística."""
    path = tmp_path / "sample.png"
    pixels = np.full((12, 16, 3), 127, dtype=np.uint8)
    Image.fromarray(pixels).save(path)
    return path
