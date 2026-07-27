"""Interface de linha de comando do MiraiOS."""

from __future__ import annotations

import argparse
from collections.abc import Sequence


VERSION = "MiraiOS CLI v0.1.0 (Projeto Hikari)"


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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Executa a CLI e retorna um código de saída apropriado."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        print("[MiraiOS] Inicializando ambiente do Projeto Hikari...")
        return 0

    # Sem comando, orienta o usuário exibindo a ajuda completa.
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
