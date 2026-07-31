"""Sessões e inferências locais com ONNX Runtime."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

from .errors import MiraiRuntimeError
from .inputs import build_input_feed
from .inspect import ensure_model_path
from .package import (
    materialize_model_artifact,
    preprocessing_from_manifest,
    validate_runtime_contract,
)
from .providers import resolve_provider_profile


def load_runtime_dependencies() -> tuple[Any, Any]:
    """Carrega ONNX Runtime e NumPy apenas quando necessários."""
    try:
        import onnxruntime as ort
    except ModuleNotFoundError as error:
        raise MiraiRuntimeError(
            "a dependência 'onnxruntime' não está instalada"
        ) from error

    try:
        import numpy as np
    except ModuleNotFoundError as error:
        raise MiraiRuntimeError("a dependência 'numpy' não está instalada") from error

    return ort, np


def create_session(
    model_path: Path,
    ort: Any,
    provider_profile: str = "auto",
) -> Any:
    """Cria uma sessão com seleção explícita e fallback declarado."""
    ensure_model_path(model_path)
    providers = resolve_provider_profile(
        provider_profile,
        ort.get_available_providers(),
    )
    options: dict[str, Any] = {"providers": providers}
    if "DmlExecutionProvider" in providers and hasattr(ort, "SessionOptions"):
        session_options = ort.SessionOptions()
        session_options.enable_mem_pattern = False
        if hasattr(ort, "ExecutionMode"):
            session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options["sess_options"] = session_options
    try:
        return ort.InferenceSession(
            str(model_path),
            **options,
        )
    except Exception as error:
        raise MiraiRuntimeError(
            f"falha ao carregar o modelo no ONNX Runtime: {error}"
        ) from error


def prepare_inference(
    model_path: Path,
    input_specs: list[str] | None = None,
    layout: str = "auto",
    preprocessing: dict[str, dict[str, Any]] | None = None,
    provider_profile: str = "auto",
) -> tuple[Any, dict[str, Any]]:
    """Cria a sessão e prepara todas as entradas do modelo."""
    ort, np = load_runtime_dependencies()
    with materialize_model_artifact(model_path) as (
        resolved_model_path,
        manifest,
    ):
        session = create_session(resolved_model_path, ort, provider_profile)
        if manifest is not None:
            validate_runtime_contract(
                manifest,
                session.get_inputs(),
                session.get_outputs(),
            )
        declared_preprocessing = (
            preprocessing
            if preprocessing is not None
            else preprocessing_from_manifest(manifest)
        )
        input_feed = build_input_feed(
            session.get_inputs(),
            input_specs,
            np,
            layout,
            declared_preprocessing,
        )
    return session, input_feed


def execute_inference(
    session: Any,
    input_feed: dict[str, Any],
) -> tuple[list[Any], float]:
    """Executa uma inferência e retorna saídas e latência em milissegundos."""
    started_at = perf_counter()
    try:
        outputs = session.run(None, input_feed)
    except Exception as error:
        raise MiraiRuntimeError(f"falha ao executar o modelo: {error}") from error
    return outputs, (perf_counter() - started_at) * 1000


def normalize_inference_result(outputs: list[Any]) -> object:
    """Converte saídas pequenas em listas e resume tensores extensos."""
    normalized: list[object] = []
    for output in outputs:
        if hasattr(output, "size") and output.size > 64:
            flat = output.reshape(-1)
            normalized.append(
                {
                    "shape": list(output.shape),
                    "dtype": str(output.dtype),
                    "preview": flat[:8].tolist(),
                }
            )
        else:
            normalized.append(
                output.tolist() if hasattr(output, "tolist") else output
            )

    result: object = normalized[0] if len(normalized) == 1 else normalized
    while isinstance(result, list) and len(result) == 1:
        result = result[0]
    return result


def run_model(
    model_path: Path,
    input_specs: list[str] | None = None,
    layout: str = "auto",
    preprocessing: dict[str, dict[str, Any]] | None = None,
    provider_profile: str = "auto",
) -> tuple[object, float]:
    """Prepara e executa uma inferência local."""
    session, input_feed = prepare_inference(
        model_path,
        input_specs,
        layout,
        preprocessing,
        provider_profile,
    )
    outputs, elapsed_ms = execute_inference(session, input_feed)
    return normalize_inference_result(outputs), elapsed_ms
