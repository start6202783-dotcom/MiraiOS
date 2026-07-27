"""Interface de linha de comando do MiraiOS."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from time import perf_counter
from typing import Any


VERSION = "MiraiOS CLI v0.5.0 (Projeto Hikari)"
ONNX_EXTENSION = ".onnx"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
BYTES_PER_KB = 1024
DEFAULT_INPUT_VALUE = "1.0"
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


def load_onnx_model(model_path: Path) -> tuple[Any, Any]:
    """Carrega a biblioteca ONNX e o modelo solicitado."""
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


def describe_value_info(value_info: Any, onnx: Any) -> tuple[str, str]:
    """Formata o shape e o tipo de uma entrada ou saída ONNX."""
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

    shape = f"[{', '.join(dimensions)}]"

    try:
        data_type = onnx.TensorProto.DataType.Name(tensor_type.elem_type)
    except (KeyError, ValueError):
        data_type = f"DESCONHECIDO ({tensor_type.elem_type})"

    return shape, data_type


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


def show_model_info(model_path: Path) -> int:
    """Carrega e exibe metadados estruturais de um modelo ONNX."""
    if error := get_model_path_error(model_path):
        return print_error(error)

    try:
        onnx, model = load_onnx_model(model_path)
    except MiraiRuntimeError as error:
        return print_error(str(error))

    print(f"[MiraiOS] Informações do modelo: {model_path.name}")
    print_value_info_section("Entradas", model.graph.input, onnx)
    print_value_info_section("Saídas", model.graph.output, onnx)
    print(f"[MiraiOS] Total de nós/camadas: {len(model.graph.node)}")
    return 0


def normalize_inference_result(outputs: list[object]) -> object:
    """Converte saídas NumPy em uma representação limpa para o terminal."""
    normalized = [
        output.tolist() if hasattr(output, "tolist") else output
        for output in outputs
    ]
    result: object = normalized[0] if len(normalized) == 1 else normalized

    while isinstance(result, list) and len(result) == 1:
        result = result[0]

    return result


def load_runtime_dependencies() -> tuple[Any, Any]:
    """Carrega as dependências do runtime somente quando necessárias."""
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


def process_image_input(image_path: Path, input_meta: Any, np: Any) -> Any:
    """Carrega e pré-processa uma imagem para ser consumida como tensor ONNX."""
    try:
        from PIL import Image
    except ModuleNotFoundError as error:
        raise MiraiRuntimeError(
            "a dependência 'Pillow' é necessária para processar imagens. "
            "Execute: python -m pip install Pillow"
        ) from error

    if not image_path.exists():
        raise MiraiRuntimeError(f"imagem de entrada não encontrada: {image_path}")

    try:
        image = Image.open(image_path).convert("RGB")
    except Exception as error:
        raise MiraiRuntimeError(f"falha ao abrir a imagem: {error}") from error

    # Extrai a dimensão esperada pelo modelo (padrão: 224x224)
    target_h, target_w = 224, 224
    shape = input_meta.shape
    if len(shape) == 4:
        if isinstance(shape[2], int) and isinstance(shape[3], int):
            target_h, target_w = shape[2], shape[3]

    image = image.resize((target_w, target_h))
    tensor = np.asarray(image, dtype=np.float32) / 255.0

    # Se a entrada for do tipo NCHW (batch, canais, altura, largura)
    if len(shape) == 4 and (shape[1] == 3 or shape[1] == "3"):
        tensor = tensor.transpose(2, 0, 1)

    return np.expand_dims(tensor, axis=0)


def prepare_inference(
    model_path: Path,
    input_value_str: str,
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

        input_meta = model_inputs[0]
        input_name = input_meta.name

        possible_image_path = Path(input_value_str)
        if possible_image_path.suffix.lower() in IMAGE_EXTENSIONS:
            print(f"[MiraiOS] Processando imagem de entrada: {possible_image_path.name}")
            input_data = process_image_input(possible_image_path, input_meta, np)
        else:
            val = float(input_value_str)
            input_data = np.asarray([val], dtype=np.float32)

    except MiraiRuntimeError:
        raise
    except Exception as error:
        raise MiraiRuntimeError(f"falha ao preparar a inferência: {error}") from error

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


def run_model(model_path: Path, input_value_str: str) -> int:
    """Carrega um modelo ONNX e executa uma inferência na CPU."""
    if error := get_model_path_error(model_path):
        return print_error(error)

    try:
        ort, np = load_runtime_dependencies()
    except MiraiRuntimeError as error:
        return print_error(str(error))

    print(f"[MiraiOS] Carregando modelo: {model_path.name}")
    print(f"[MiraiOS] Entrada fornecida: {input_value_str}")

    try:
        session, input_feed = prepare_inference(
            model_path,
            input_value_str,
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

    info_parser = subparsers.add_parser(
        "info",
        help="exibe informações estruturais de um modelo ONNX",
        description="Lista entradas, saídas e nós do grafo de um modelo ONNX.",
    )
    info_parser.add_argument(
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
        dest="input_value_str",
        type=str,
        default=DEFAULT_INPUT_VALUE,
        metavar="VALOR_OU_IMAGEM",
        help="valor numérico ou caminho para imagem (.jpg, .png) (padrão: 1.0)",
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

    if args.command == "info":
        return show_model_info(args.model_path)

    if args.command == "run":
        return run_model(args.model_path, args.input_value_str)

    if args.command == "benchmark":
        return benchmark_model(args.model_path, args.runs)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
