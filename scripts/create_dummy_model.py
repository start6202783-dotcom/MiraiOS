"""Gera um modelo ONNX mínimo para testes do MiraiOS."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper


OUTPUT_PATH = Path(__file__).resolve().parents[1] / "examples" / "dummy_model.onnx"
DISPLAY_PATH = Path("examples/dummy_model.onnx")


def create_dummy_model() -> onnx.ModelProto:
    """Cria um modelo que soma 1.0 a um tensor de entrada."""
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
    add_node = helper.make_node(
        "Add",
        inputs=["input", "constant"],
        outputs=["output"],
    )

    graph = helper.make_graph(
        nodes=[add_node],
        name="MiraiOSDummyModel",
        inputs=[model_input],
        outputs=[model_output],
        initializer=[constant],
    )
    return helper.make_model(
        graph,
        producer_name="MiraiOS",
        opset_imports=[helper.make_opsetid("", 13)],
    )


def main() -> None:
    """Valida e salva o modelo de teste no diretório examples."""
    model = create_dummy_model()
    onnx.checker.check_model(model)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(model, OUTPUT_PATH)

    print(f"[MiraiOS] Modelo de teste gerado com sucesso em: {DISPLAY_PATH}")


if __name__ == "__main__":
    main()
