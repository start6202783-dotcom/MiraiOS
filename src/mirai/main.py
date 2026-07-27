"""Interface de linha de comando do MiraiOS."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from typing import Any


VERSION = "MiraiOS CLI v0.2.0 (Projeto Hikari)"
ONNX_EXTENSION = ".onnx"
BYTES_PER_KB = 1024
DEFAULT_INPUT_VALUE = 1.0
DEFAULT_BENCHMARK_RUNS = 50


class MiraiRuntimeError(RuntimeError):
    """Representa uma falha controlada na execução do runtime."""


def initialize_environment() -> int:
    """Inicializa o ambiente local do Projeto Hikari."""
    print("[MiraiOS] Inicializando ambiente do Projeto Hikari...")
    return 0


def get_model_path_error(model_path: Path) -> str | None:
    """Retorna uma descrição do erro do caminho ou ``None`` se ele for válido."""
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


def print_error(message: str) -> int:
    """Exibe um erro padronizado e retorna código de falha."""
    print(f"[MiraiOS] Erro: {message}", file=sys.stderr)
    return 1


def validate_model(model_path: Path) -> int:
    """Valida a existência e a extensão de um modelo ONNX."""
    if error := get_model_path_error(model_path):
        return print_error(error)

    try:
        size_kb = model_path.stat().st_size / BYTES_PER_KB
    except OSError as error:
        return print_error(f"não foi possível acessar o arquivo: {error}")

    print(
        f"[MiraiOS] Modelo ONNX válido encontrado: {model_path.name} "
        f"(Tamanho: {size_kb:.2f} KB)"
    )
    return 0


def normalize_inference_result(outputs: list[object]) -> object:
    """Converte saídas NumPy em uma representação limpa para o terminal."""
    normalized = [
        output.tolist() if hasattr(output, "tolist") else output
        for output in outputs
    ]
    result: object = normalized[0] if len(normalized) == 1 else normalized

    # Remove dimensões unitárias externas, como ``[2.0]``.
    while isinstance(result, list) and len(result) == 1:
        result = result[0]

    return result


def load_runtime_dependencies() -> tuple[Any, Any]:
    """Carrega as dependências do runtime somente quando necessárias."""
    # Imports tardios mantêm os demais comandos disponíveis sem o runtime.
    try:
        import onnxruntime as ort
    except ModuleNotFoundError as error:
        raise MiraiRuntimeError(
            "a dependência 'onnxruntime' não está instalada. "
            "Execute: python -m pip install onnxruntime numpy"
        ) from error

    try:
        import numpy as np
    except ModuleNotFoundError as error:
        raise MiraiRuntimeError(
            "a dependência 'numpy' não está instalada. "
            "Execute: python -m pip install numpy"
        ) from error

    return ort, np


def prepare_inference(
    model_path: Path,
    input_value: float,
    ort: Any,
    np: Any,
) -> tuple[Any, dict[str, Any]]:
    """Carrega o modelo uma única vez e prepara seu tensor de entrada."""
    try:
        session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
        model_inputs = session.get_inputs()
        if not model_inputs:
            raise ValueError("o modelo não possui entradas")

        input_name = model_inputs[0].name
        input_data = np.asarray([input_value], dtype=np.float32)
    except Exception as error:
        raise MiraiRuntimeError(f"falha ao carregar o modelo: {error}") from error

    return session, {input_name: input_data}


def execute_inference(
    session: Any,
    input_feed: dict[str, Any],
) -> tuple[list[object], float]:
    """Executa uma inferência e retorna suas saídas e latência em ms."""
    started_at = perf_counter()
    try:
        outputs = session.run(None, input_feed)
    except Exception as error:
        raise MiraiRuntimeError(f"falha ao executar o modelo: {error}") from error
    elapsed_ms = (perf_counter() - started_at) * 1000

    return outputs, elapsed_ms


def run_model(model_path: Path, input_value: float) -> int:
    """Carrega um modelo ONNX e executa uma inferência na CPU."""
    if error := get_model_path_error(model_path):
        return print_error(error)

    try:
        ort, np = load_runtime_dependencies()
    except MiraiRuntimeError as error:
        return print_error(str(error))

    print(f"[MiraiOS] Carregando modelo: {model_path.name}")
    print(f"[MiraiOS] Entrada fornecida: {input_value}")

    try:
        session, input_feed = prepare_inference(
            model_path,
            input_value,
            ort,
            np,
        )
        outputs, elapsed_ms = execute_inference(session, input_feed)
    except MiraiRuntimeError as error:
        return print_error(str(error))

    result = normalize_inference_result(outputs)
    print(f"[MiraiOS] Resultado da inferência: {result}")
    print(f"[MiraiOS] Tempo de inferência: {elapsed_ms:.2f} ms")
    return 0


def benchmark_model(model_path: Path, runs: int) -> int:
    """Mede a latência e a vazão de inferência de um modelo ONNX."""
    if error := get_model_path_error(model_path):
        return print_error(error)

    try:
        ort, np = load_runtime_dependencies()
    except MiraiRuntimeError as error:
        return print_error(str(error))

    print(f"[MiraiOS] Benchmark do modelo: {model_path.name}")
    print(f"[MiraiOS] Número de execuções: {runs}")

    try:
        session, input_feed = prepare_inference(
            model_path,
            DEFAULT_INPUT_VALUE,
            ort,
            np,
        )

        started_at = perf_counter()
        for _ in range(runs):
            session.run(None, input_feed)
        total_ms = (perf_counter() - started_at) * 1000
    except MiraiRuntimeError as error:
        return print_error(str(error))
    except Exception as error:
        return print_error(f"falha durante o benchmark: {error}")

    average_ms = total_ms / runs
    inferences_per_second = runs * 1000 / total_ms if total_ms > 0 else float("inf")

    print(f"[MiraiOS] Tempo total de execução: {total_ms:.2f} ms")
    print(f"[MiraiOS] Tempo médio por inferência: {average_ms:.2f} ms")
    print(
        "[MiraiOS] Inferências por segundo (FPS/IPS): "
        f"{inferences_per_second:.2f}"
    )
    return 0


def positive_int(value: str) -> int:
    """Converte um argumento em inteiro estritamente positivo."""
    try:
        parsed_value = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("deve ser um número inteiro") from error

    if parsed_value <= 0:
        raise argparse.ArgumentTypeError("deve ser maior que zero")

    return parsed_value


def build_parser() -> argparse.ArgumentParser:
    """Cria e configura o parser principal da CLI."""
    parser = argparse.ArgumentParser(
        prog="mirai",
        description="CLI do MiraiOS — The Future Runs Local",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=VERSION,
        help="exibe a versão instalada e encerra",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="comandos disponíveis",
        metavar="COMANDO",
    )
    subparsers.add_parser(
        "init",
        help="inicializa o ambiente do Projeto Hikari",
        description="Inicializa o ambiente local do Projeto Hikari.",
    )

    validate_parser = subparsers.add_parser(
        "validate",
        help="valida um arquivo de modelo ONNX",
        description=(
            "Verifica a existência, a extensão e o tamanho de um modelo ONNX."
        ),
    )
    validate_parser.add_argument(
        "model_path",
        type=Path,
        metavar="ARQUIVO",
        help="caminho para o arquivo .onnx",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="executa uma inferência com um modelo ONNX",
        description="Carrega um modelo ONNX e executa uma inferência local.",
    )
    run_parser.add_argument(
        "model_path",
        type=Path,
        metavar="ARQUIVO",
        help="caminho para o arquivo .onnx",
    )
    run_parser.add_argument(
        "--input",
        dest="input_value",
        type=float,
        default=DEFAULT_INPUT_VALUE,
        metavar="VALOR",
        help="valor numérico de entrada (padrão: 1.0)",
    )

    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="mede o desempenho de um modelo ONNX",
        description="Executa inferências repetidas e calcula latência e vazão.",
    )
    benchmark_parser.add_argument(
        "model_path",
        type=Path,
        metavar="ARQUIVO",
        help="caminho para o arquivo .onnx",
    )
    benchmark_parser.add_argument(
        "--runs",
        type=positive_int,
        default=DEFAULT_BENCHMARK_RUNS,
        metavar="QUANTIDADE",
        help="número de inferências (padrão: 50)",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Executa a CLI e retorna um código de saída apropriado."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return initialize_environment()

    if args.command == "validate":
        return validate_model(args.model_path)

    if args.command == "run":
        return run_model(args.model_path, args.input_value)

    if args.command == "benchmark":
        return benchmark_model(args.model_path, args.runs)

    # Sem comando, orienta o usuário exibindo a ajuda completa.
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
