"""Interface de linha de comando do MiraiOS."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .benchmark import (
    DEFAULT_BENCHMARK_RUNS,
    DEFAULT_WARMUP_RUNS,
    benchmark_model,
)
from .errors import MiraiRuntimeError
from .inspect import show_model_info, validate_model
from .runtime import run_model


VERSION = f"MiraiOS CLI v{__version__} (Projeto Hikari)"


def positive_int(value: str) -> int:
    """Converte um argumento em inteiro estritamente positivo."""
    try:
        parsed_value = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("deve ser um número inteiro") from error
    if parsed_value <= 0:
        raise argparse.ArgumentTypeError("deve ser maior que zero")
    return parsed_value


def non_negative_int(value: str) -> int:
    """Converte um argumento em inteiro maior ou igual a zero."""
    try:
        parsed_value = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("deve ser um número inteiro") from error
    if parsed_value < 0:
        raise argparse.ArgumentTypeError("não pode ser negativo")
    return parsed_value


def _add_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input",
        dest="input_specs",
        action="append",
        metavar="VALOR_OU_NOME=VALOR",
        help=(
            "valor, array JSON ou imagem; repita para múltiplas entradas "
            "(ex.: --input x=1 --input y=2)"
        ),
    )
    parser.add_argument(
        "--layout",
        choices=("auto", "nchw", "nhwc"),
        default="auto",
        help="layout de entradas de imagem (padrão: detecção automática)",
    )


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
        help="confirma a inicialização do ambiente do Projeto Hikari",
    )

    validate_parser = subparsers.add_parser(
        "validate",
        help="valida integralmente um modelo ONNX",
    )
    validate_parser.add_argument("model_path", type=Path, metavar="ARQUIVO")

    info_parser = subparsers.add_parser(
        "info",
        help="exibe entradas, saídas, tipos, shapes e nós do modelo",
    )
    info_parser.add_argument("model_path", type=Path, metavar="ARQUIVO")

    run_parser = subparsers.add_parser(
        "run",
        help="executa uma inferência local",
    )
    run_parser.add_argument("model_path", type=Path, metavar="ARQUIVO")
    _add_input_arguments(run_parser)

    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="mede latência e vazão do modelo",
    )
    benchmark_parser.add_argument("model_path", type=Path, metavar="ARQUIVO")
    benchmark_parser.add_argument(
        "--runs",
        type=positive_int,
        default=DEFAULT_BENCHMARK_RUNS,
        metavar="QUANTIDADE",
        help=f"inferências medidas (padrão: {DEFAULT_BENCHMARK_RUNS})",
    )
    benchmark_parser.add_argument(
        "--warmup",
        type=non_negative_int,
        default=DEFAULT_WARMUP_RUNS,
        metavar="QUANTIDADE",
        help=f"inferências de aquecimento (padrão: {DEFAULT_WARMUP_RUNS})",
    )
    _add_input_arguments(benchmark_parser)
    return parser


def print_error(message: str) -> int:
    """Exibe um erro controlado e retorna código de falha."""
    print(f"[MiraiOS] Erro: {message}", file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Executa a CLI e retorna um código de saída apropriado."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "init":
            print("[MiraiOS] Ambiente do Projeto Hikari pronto.")
            return 0

        if args.command == "validate":
            size_kb = validate_model(args.model_path)
            print(
                f"[MiraiOS] Modelo ONNX válido: {args.model_path.name} "
                f"({size_kb:.2f} KB)"
            )
            return 0

        if args.command == "info":
            show_model_info(args.model_path)
            return 0

        if args.command == "run":
            print(f"[MiraiOS] Carregando modelo: {args.model_path.name}")
            result, elapsed_ms = run_model(
                args.model_path,
                args.input_specs,
                args.layout,
            )
            print(f"[MiraiOS] Resultado da inferência: {result}")
            print(f"[MiraiOS] Tempo de inferência: {elapsed_ms:.2f} ms")
            return 0

        if args.command == "benchmark":
            print(f"[MiraiOS] Benchmark do modelo: {args.model_path.name}")
            stats = benchmark_model(
                args.model_path,
                args.runs,
                args.warmup,
                args.input_specs,
                args.layout,
            )
            print(f"[MiraiOS] Aquecimento: {stats.warmup_runs} execuções")
            print(f"[MiraiOS] Inferências medidas: {stats.runs}")
            print(f"[MiraiOS] Tempo total: {stats.total_ms:.2f} ms")
            print(f"[MiraiOS] Latência média: {stats.average_ms:.2f} ms")
            print(f"[MiraiOS] Mediana: {stats.median_ms:.2f} ms")
            print(f"[MiraiOS] P95: {stats.p95_ms:.2f} ms")
            print(
                "[MiraiOS] Inferências por segundo (IPS): "
                f"{stats.inferences_per_second:.2f}"
            )
            return 0
    except MiraiRuntimeError as error:
        return print_error(str(error))

    parser.print_help()
    return 0
