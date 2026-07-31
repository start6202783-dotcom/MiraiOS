"""Conversão de valores da CLI em tensores compatíveis com ONNX Runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import MiraiRuntimeError


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
NUMPY_EXTENSIONS = {".npy"}
DEFAULT_INPUT_VALUE = "1.0"
DEFAULT_IMAGE_SIZE = 224


def numpy_dtype(type_name: str, np: Any) -> Any:
    """Converte o tipo informado pelo ONNX Runtime em dtype NumPy."""
    mapping = {
        "tensor(bool)": np.bool_,
        "tensor(double)": np.float64,
        "tensor(float)": np.float32,
        "tensor(float16)": np.float16,
        "tensor(int8)": np.int8,
        "tensor(int16)": np.int16,
        "tensor(int32)": np.int32,
        "tensor(int64)": np.int64,
        "tensor(string)": np.object_,
        "tensor(uint8)": np.uint8,
        "tensor(uint16)": np.uint16,
        "tensor(uint32)": np.uint32,
        "tensor(uint64)": np.uint64,
    }
    try:
        return mapping[type_name]
    except KeyError as error:
        raise MiraiRuntimeError(
            f"tipo de entrada ainda não suportado: {type_name}"
        ) from error


def _is_fixed_dimension(dimension: object) -> bool:
    return isinstance(dimension, int) and dimension > 0


def _resolved_shape(shape: list[object]) -> tuple[int, ...]:
    return tuple(
        int(dimension) if _is_fixed_dimension(dimension) else 1
        for dimension in shape
    )


def _validate_tensor_shape(array: Any, input_meta: Any) -> None:
    expected = list(input_meta.shape)
    if array.ndim != len(expected):
        raise MiraiRuntimeError(
            f"entrada '{input_meta.name}' possui rank {array.ndim}, "
            f"mas o modelo espera rank {len(expected)} ({expected})"
        )

    incompatible = [
        (index, actual, wanted)
        for index, (actual, wanted) in enumerate(zip(array.shape, expected))
        if _is_fixed_dimension(wanted) and actual != wanted
    ]
    if incompatible:
        details = ", ".join(
            f"dimensão {index}: recebido {actual}, esperado {wanted}"
            for index, actual, wanted in incompatible
        )
        raise MiraiRuntimeError(
            f"shape incompatível para a entrada '{input_meta.name}' ({details})"
        )


def _parse_literal(value: str, expected_dtype: Any, np: Any) -> object:
    if expected_dtype is np.object_:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        return parsed

    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise MiraiRuntimeError(
            f"entrada numérica ou JSON inválida: {value!r}"
        ) from error


def process_numeric_input(value: str, input_meta: Any, np: Any) -> Any:
    """Converte um escalar ou array JSON usando dtype e shape do modelo."""
    dtype = numpy_dtype(input_meta.type, np)
    parsed = _parse_literal(value, dtype, np)

    try:
        array = np.asarray(parsed, dtype=dtype)
    except (TypeError, ValueError) as error:
        raise MiraiRuntimeError(
            f"valor incompatível com a entrada '{input_meta.name}': {error}"
        ) from error

    expected_shape = list(input_meta.shape)
    if array.ndim == 0 and expected_shape:
        array = np.full(_resolved_shape(expected_shape), array.item(), dtype=dtype)
    elif array.ndim != len(expected_shape):
        resolved = _resolved_shape(expected_shape)
        expected_size = int(np.prod(resolved)) if resolved else 1
        if array.size == expected_size:
            array = array.reshape(resolved)

    _validate_tensor_shape(array, input_meta)
    return array


def process_numpy_input(path: Path, input_meta: Any, np: Any) -> Any:
    """Carrega um tensor NPY sem permitir desserialização de objetos Python."""
    if not path.is_file():
        raise MiraiRuntimeError(f"arquivo NPY não encontrado: {path}")
    try:
        array = np.load(path, allow_pickle=False)
    except (OSError, ValueError) as error:
        raise MiraiRuntimeError(f"arquivo NPY inválido ou inseguro: {error}") from error
    if array.dtype.hasobject:
        raise MiraiRuntimeError("arquivos NPY com objetos não são permitidos")
    expected_dtype = numpy_dtype(input_meta.type, np)
    try:
        array = np.asarray(array, dtype=expected_dtype)
    except (TypeError, ValueError) as error:
        raise MiraiRuntimeError(
            f"tensor NPY incompatível com '{input_meta.name}': {error}"
        ) from error
    _validate_tensor_shape(array, input_meta)
    return array


def detect_image_layout(shape: list[object], requested: str) -> str:
    """Detecta NCHW/NHWC ou respeita o layout explicitamente solicitado."""
    if len(shape) != 4:
        raise MiraiRuntimeError(
            f"entrada de imagem deve possuir rank 4; shape recebido: {shape}"
        )
    if requested in {"nchw", "nhwc"}:
        return requested

    channel_sizes = {1, 3, 4}
    nchw = shape[1] in channel_sizes
    nhwc = shape[3] in channel_sizes

    if nchw and not nhwc:
        return "nchw"
    if nhwc and not nchw:
        return "nhwc"
    if nchw and nhwc:
        raise MiraiRuntimeError(
            f"layout ambíguo para o shape {shape}; use --layout nchw ou nhwc"
        )
    raise MiraiRuntimeError(
        f"não foi possível detectar o layout da imagem para {shape}; "
        "use --layout nchw ou nhwc"
    )


def process_image_input(
    image_path: Path,
    input_meta: Any,
    np: Any,
    layout: str = "auto",
    preprocessing: dict[str, Any] | None = None,
) -> Any:
    """Carrega uma imagem respeitando layout, dtype e shape do modelo."""
    try:
        from PIL import Image
    except ModuleNotFoundError as error:
        raise MiraiRuntimeError(
            "a dependência 'Pillow' é necessária para processar imagens"
        ) from error

    if not image_path.exists() or not image_path.is_file():
        raise MiraiRuntimeError(f"imagem de entrada não encontrada: {image_path}")

    shape = list(input_meta.shape)
    resolved_layout = (
        str(preprocessing["layout"])
        if preprocessing is not None
        else detect_image_layout(shape, layout)
    )
    if resolved_layout == "nchw":
        batch, channels, target_h, target_w = shape
    else:
        batch, target_h, target_w, channels = shape

    if _is_fixed_dimension(batch) and batch != 1:
        raise MiraiRuntimeError(
            "uma única imagem só pode alimentar modelos com batch 1 ou dinâmico"
        )

    channel_count = int(channels) if _is_fixed_dimension(channels) else 3
    if channel_count not in {1, 3, 4}:
        raise MiraiRuntimeError(
            f"quantidade de canais de imagem não suportada: {channel_count}"
        )

    height = int(target_h) if _is_fixed_dimension(target_h) else DEFAULT_IMAGE_SIZE
    width = int(target_w) if _is_fixed_dimension(target_w) else DEFAULT_IMAGE_SIZE
    image_mode = {1: "L", 3: "RGB", 4: "RGBA"}[channel_count]

    try:
        with Image.open(image_path) as source:
            image = source.convert(image_mode).resize((width, height))
            array = np.asarray(image)
    except Exception as error:
        raise MiraiRuntimeError(f"falha ao abrir a imagem: {error}") from error

    if channel_count == 1:
        array = np.expand_dims(array, axis=-1)

    dtype = numpy_dtype(input_meta.type, np)
    array = array.astype(dtype, copy=False)
    if preprocessing is not None:
        if preprocessing.get("kind") != "image":
            raise MiraiRuntimeError(
                f"entrada '{input_meta.name}' não aceita imagens "
                "segundo o manifesto"
            )
        if np.issubdtype(dtype, np.floating):
            scale = np.asarray(preprocessing["scale"], dtype=dtype)
            mean = np.asarray(preprocessing["mean"], dtype=dtype)
            std = np.asarray(preprocessing["std"], dtype=dtype)
            array = (array * scale - mean) / std
    elif np.issubdtype(dtype, np.floating):
        array = array / np.asarray(255.0, dtype=dtype)

    if resolved_layout == "nchw":
        array = array.transpose(2, 0, 1)
    array = np.expand_dims(array, axis=0)
    _validate_tensor_shape(array, input_meta)
    return array


def resolve_input_specs(
    model_inputs: list[Any],
    input_specs: list[str] | None,
) -> dict[str, str]:
    """Associa argumentos posicionais ou ``nome=valor`` às entradas do modelo."""
    if not model_inputs:
        raise MiraiRuntimeError("o modelo não possui entradas")

    if not input_specs:
        if len(model_inputs) == 1:
            return {model_inputs[0].name: DEFAULT_INPUT_VALUE}
        names = ", ".join(input_meta.name for input_meta in model_inputs)
        raise MiraiRuntimeError(
            "o modelo possui múltiplas entradas; informe uma opção --input "
            f"para cada uma: {names}"
        )

    known_names = {input_meta.name for input_meta in model_inputs}
    assigned: dict[str, str] = {}
    positional: list[str] = []

    for spec in input_specs:
        possible_name, separator, possible_value = spec.partition("=")
        if separator:
            if possible_name not in known_names:
                raise MiraiRuntimeError(
                    f"entrada desconhecida: {possible_name}; disponíveis: "
                    f"{', '.join(sorted(known_names))}"
                )
            if possible_name in assigned:
                raise MiraiRuntimeError(
                    f"entrada informada mais de uma vez: {possible_name}"
                )
            assigned[possible_name] = possible_value
            continue
        positional.append(spec)

    remaining = [
        input_meta.name
        for input_meta in model_inputs
        if input_meta.name not in assigned
    ]
    if len(positional) > len(remaining):
        raise MiraiRuntimeError("foram informados mais valores do que entradas")

    for name, value in zip(remaining, positional):
        assigned[name] = value

    missing = [
        input_meta.name
        for input_meta in model_inputs
        if input_meta.name not in assigned
    ]
    if missing:
        raise MiraiRuntimeError(
            f"faltam valores para as entradas: {', '.join(missing)}"
        )
    return assigned


def build_input_feed(
    model_inputs: list[Any],
    input_specs: list[str] | None,
    np: Any,
    layout: str = "auto",
    preprocessing: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Monta o dicionário de tensores consumido pelo ONNX Runtime."""
    values = resolve_input_specs(model_inputs, input_specs)
    feed: dict[str, Any] = {}

    for input_meta in model_inputs:
        raw_value = values[input_meta.name]
        possible_image = Path(raw_value)
        profile = (
            preprocessing.get(input_meta.name)
            if preprocessing is not None
            else None
        )
        if possible_image.suffix.lower() in IMAGE_EXTENSIONS:
            feed[input_meta.name] = process_image_input(
                possible_image,
                input_meta,
                np,
                layout,
                profile,
            )
        elif possible_image.suffix.lower() in NUMPY_EXTENSIONS:
            if profile is not None and profile.get("kind") == "image":
                raise MiraiRuntimeError(
                    f"entrada '{input_meta.name}' exige uma imagem segundo o manifesto"
                )
            feed[input_meta.name] = process_numpy_input(
                possible_image,
                input_meta,
                np,
            )
        else:
            if profile is not None and profile.get("kind") == "image":
                raise MiraiRuntimeError(
                    f"entrada '{input_meta.name}' exige uma imagem "
                    "segundo o manifesto"
                )
            feed[input_meta.name] = process_numeric_input(
                raw_value,
                input_meta,
                np,
            )
    return feed
