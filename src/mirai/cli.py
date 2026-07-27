"""Interface de linha de comando do MiraiOS."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .agent import run_agent
from .agent_client import deploy_model, get_agent_info, get_agent_logs
from .benchmark import (
    DEFAULT_BENCHMARK_RUNS,
    DEFAULT_WARMUP_RUNS,
    benchmark_model,
)
from .devices import add_device, get_device, load_devices, remove_device
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


def tcp_port(value: str) -> int:
    """Converte um argumento em uma porta TCP válida."""
    parsed_value = positive_int(value)
    if parsed_value > 65535:
        raise argparse.ArgumentTypeError("deve estar entre 1 e 65535")
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

    device_parser = subparsers.add_parser(
        "device",
        help="gerencia dispositivos com Mirai Agent",
    )
    device_subparsers = device_parser.add_subparsers(
        dest="device_command",
        title="ações de dispositivo",
        metavar="AÇÃO",
        required=True,
    )

    device_add_parser = device_subparsers.add_parser(
        "add",
        help="cadastra um Mirai Agent",
    )
    device_add_parser.add_argument("name", metavar="NOME")
    device_add_parser.add_argument(
        "--url",
        required=True,
        metavar="URL",
        help="URL raiz do Agent (ex.: http://127.0.0.1:8080)",
    )
    device_add_parser.add_argument(
        "--replace",
        action="store_true",
        help="substitui um cadastro existente com o mesmo nome",
    )

    device_subparsers.add_parser(
        "list",
        help="lista os dispositivos cadastrados",
    )

    device_info_parser = device_subparsers.add_parser(
        "info",
        help="consulta informações do dispositivo",
    )
    device_info_parser.add_argument("name", metavar="NOME")

    device_remove_parser = device_subparsers.add_parser(
        "remove",
        help="remove um dispositivo do registro",
    )
    device_remove_parser.add_argument("name", metavar="NOME")

    deploy_parser = subparsers.add_parser(
        "deploy",
        help="valida e envia um modelo ONNX para um dispositivo",
    )
    deploy_parser.add_argument("model_path", type=Path, metavar="ARQUIVO")
    deploy_parser.add_argument(
        "--device",
        required=True,
        dest="device_name",
        metavar="NOME",
        help="dispositivo cadastrado com 'mirai device add'",
    )

    logs_parser = subparsers.add_parser(
        "logs",
        help="exibe os eventos recentes de um dispositivo",
    )
    logs_parser.add_argument(
        "--device",
        required=True,
        dest="device_name",
        metavar="NOME",
    )
    logs_parser.add_argument(
        "--limit",
        type=positive_int,
        default=20,
        metavar="QUANTIDADE",
        help="quantidade de eventos, entre 1 e 200 (padrão: 20)",
    )

    agent_parser = subparsers.add_parser(
        "agent",
        help="executa o Mirai Agent neste dispositivo",
    )
    agent_subparsers = agent_parser.add_subparsers(
        dest="agent_command",
        title="ações do Agent",
        metavar="AÇÃO",
        required=True,
    )
    agent_start_parser = agent_subparsers.add_parser(
        "start",
        help="inicia a API local do Agent",
    )
    agent_start_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="endereço de escuta (padrão seguro: 127.0.0.1)",
    )
    agent_start_parser.add_argument(
        "--port",
        type=tcp_port,
        default=8080,
        help="porta TCP (padrão: 8080)",
    )
    agent_start_parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(".mirai-agent"),
        metavar="DIRETÓRIO",
        help="armazenamento de modelos e eventos",
    )
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

        if args.command == "device":
            if args.device_command == "add":
                device = add_device(
                    args.name,
                    args.url,
                    replace=args.replace,
                )
                print(
                    f"[MiraiOS] Dispositivo cadastrado: "
                    f"{device.name} ({device.url})"
                )
                return 0

            if args.device_command == "list":
                devices = load_devices()
                if not devices:
                    print("[MiraiOS] Nenhum dispositivo cadastrado.")
                    return 0
                print("[MiraiOS] Dispositivos cadastrados:")
                for device in devices.values():
                    print(f"- {device.name}: {device.url}")
                return 0

            if args.device_command == "info":
                device = get_device(args.name)
                info = get_agent_info(device)
                print(f"[MiraiOS] Dispositivo: {device.name}")
                print(f"[MiraiOS] Agent: {device.url}")
                print(
                    "[MiraiOS] Sistema: "
                    f"{info.get('system', 'desconhecido')} "
                    f"{info.get('release', '')}".rstrip()
                )
                print(
                    "[MiraiOS] Arquitetura: "
                    f"{info.get('machine', 'desconhecida')}"
                )
                providers = info.get("providers") or []
                provider_text = ", ".join(providers) if providers else "nenhum"
                print(f"[MiraiOS] Providers: {provider_text}")
                return 0

            if args.device_command == "remove":
                device = remove_device(args.name)
                print(f"[MiraiOS] Dispositivo removido: {device.name}")
                return 0

        if args.command == "deploy":
            device = get_device(args.device_name)
            print(
                f"[MiraiOS] Enviando {args.model_path.name} "
                f"para {device.name}..."
            )
            deployment = deploy_model(device, args.model_path)
            print(
                "[MiraiOS] Deployment pronto: "
                f"{deployment['deployment_id']}"
            )
            print(f"[MiraiOS] SHA-256: {deployment['sha256']}")
            providers = deployment.get("providers") or []
            if providers:
                print(f"[MiraiOS] Providers: {', '.join(providers)}")
            return 0

        if args.command == "logs":
            if args.limit > 200:
                raise MiraiRuntimeError("limit deve estar entre 1 e 200")
            device = get_device(args.device_name)
            events = get_agent_logs(device, args.limit)
            if not events:
                print(f"[MiraiOS] Nenhum evento em {device.name}.")
                return 0
            print(f"[MiraiOS] Eventos recentes de {device.name}:")
            for event in events:
                timestamp = event.get("timestamp", "sem-data")
                status = event.get("status", "desconhecido")
                event_type = event.get("type", "evento")
                model = event.get("model")
                suffix = f" — {model}" if model else ""
                print(f"- {timestamp} | {event_type} | {status}{suffix}")
            return 0

        if args.command == "agent" and args.agent_command == "start":
            run_agent(args.host, args.port, args.data_dir)
            return 0
    except MiraiRuntimeError as error:
        return print_error(str(error))

    parser.print_help()
    return 0
