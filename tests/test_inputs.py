"""Testes da preparação de entradas numéricas e de imagem."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from onnx import TensorProto

from mirai.errors import MiraiRuntimeError
from mirai.runtime import prepare_inference, run_model


ModelFactory = Callable[[str, list[int | str | None], int], Path]


def test_scalar_is_expanded_to_model_shape(model_factory: ModelFactory) -> None:
    model = model_factory("matrix", [1, 1])
    session, feed = prepare_inference(model, ["5.0"])

    assert feed["input"].shape == (1, 1)
    assert feed["input"].dtype == np.float32
    assert session.run(None, feed)[0].tolist() == [[5.0]]


def test_json_array_preserves_integer_dtype(model_factory: ModelFactory) -> None:
    model = model_factory("integers", [1, 3], TensorProto.INT64)
    _, feed = prepare_inference(model, ["[1, 2, 3]"])

    assert feed["input"].shape == (1, 3)
    assert feed["input"].dtype == np.int64
    assert feed["input"].tolist() == [[1, 2, 3]]


def test_named_multiple_inputs_are_supported(multi_input_model: Path) -> None:
    result, _ = run_model(
        multi_input_model,
        ["y=7.0", "x=5.0"],
    )

    assert result == 12.0


def test_positional_multiple_inputs_are_supported(multi_input_model: Path) -> None:
    result, _ = run_model(multi_input_model, ["5.0", "7.0"])

    assert result == 12.0


def test_missing_multiple_input_is_reported(multi_input_model: Path) -> None:
    with pytest.raises(MiraiRuntimeError, match="faltam valores"):
        prepare_inference(multi_input_model, ["x=5.0"])


def test_unknown_named_input_is_reported(multi_input_model: Path) -> None:
    with pytest.raises(MiraiRuntimeError, match="entrada desconhecida: z"):
        prepare_inference(multi_input_model, ["x=5.0", "z=7.0"])


def test_nchw_float_image_is_prepared(
    model_factory: ModelFactory,
    sample_image: Path,
) -> None:
    model = model_factory("nchw", [1, 3, 8, 10])
    _, feed = prepare_inference(model, [str(sample_image)])

    assert feed["input"].shape == (1, 3, 8, 10)
    assert feed["input"].dtype == np.float32
    assert 0.49 < float(feed["input"].mean()) < 0.50


def test_nhwc_float_image_is_prepared(
    model_factory: ModelFactory,
    sample_image: Path,
) -> None:
    model = model_factory("nhwc", [1, 8, 10, 3])
    _, feed = prepare_inference(model, [str(sample_image)])

    assert feed["input"].shape == (1, 8, 10, 3)
    assert feed["input"].dtype == np.float32


def test_uint8_image_keeps_original_scale(
    model_factory: ModelFactory,
    sample_image: Path,
) -> None:
    model = model_factory("uint8", [1, 3, 8, 10], TensorProto.UINT8)
    _, feed = prepare_inference(model, [str(sample_image)])

    assert feed["input"].shape == (1, 3, 8, 10)
    assert feed["input"].dtype == np.uint8
    assert int(feed["input"].mean()) == 127


def test_ambiguous_image_layout_requires_explicit_choice(
    model_factory: ModelFactory,
    sample_image: Path,
) -> None:
    model = model_factory("ambiguous", [1, 3, 8, 3])

    with pytest.raises(MiraiRuntimeError, match="layout ambíguo"):
        prepare_inference(model, [str(sample_image)])

    _, feed = prepare_inference(model, [str(sample_image)], layout="nchw")
    assert feed["input"].shape == (1, 3, 8, 3)
