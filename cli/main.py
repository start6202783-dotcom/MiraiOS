"""Interface de linha de comando do MiraiOS."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


VERSION = "MiraiOS CLI v0.1.0 (Projeto Hikari)"
ONNX_EXTENSION = ".onnx"
BYTES_PER_KB = 1024


def initialize_environment() -> int:
    """Inicializa o ambiente local do Projeto Hikari."""
    print("[MiraiOS] Inicializando ambiente do Projeto Hikari...")
    return 0


def validate_model(model_path: Path) -> int:
    """Valida a existência e a extensão de um modelo ONNX."""
    if not model_path.exists():
        print(
            f"[MiraiOS] Erro: arquivo não encontrado: {model_path}",
            file=sys.stderr,
        )
        return 1

    if not model_path.is_file():
        print(
            f"[MiraiOS] Erro: o caminho informado não é um arquivo: {model_path}",
            file=sys.stderr,
        )
        return 1

    if model_path.suffix.lower() != ONNX_EXTENSION:
        print(
            f"[MiraiOS] Erro: o arquivo deve possuir a extensão {ONNX_EXTENSION}: "
            f"{model_path.name}",
            file=sys.stderr,
        )
        return 1

    try:
        size_kb = model_path.stat().st_size / BYTES_PER_KB
    except OSError as error:
        print(
            f"[MiraiOS] Erro: não foi possível acessar o arquivo: {error}",
            file=sys.stderr,
        )
        return 1

    print(
        f"[MiraiOS] Modelo ONNX válido encontrado: {model_path.name} "
        f"(Tamanho: {size_kb:.2f} KB)"
    )
    return 0


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
        description="Verifica a existência, a extensão e o tamanho de um modelo ONNX.",
    )
    validate_parser.add_argument(
        "model_path",
        type=Path,
        metavar="ARQUIVO",
        help="caminho para o arquivo .onnx",
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

    # Sem comando, orienta o usuário exibindo a ajuda completa.
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
