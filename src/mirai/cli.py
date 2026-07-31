"""Interface de linha de comando do MiraiOS."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .agent import run_agent
from .agent_client import (
    activate_deployment,
    deploy_model,
    doctor_device,
    get_agent_info,
    get_agent_logs,
    get_deployment_status,
    pair_device,
    revoke_remote_device,
    run_remote_model,
)
from .benchmark import (
    DEFAULT_BENCHMARK_RUNS,
    DEFAULT_WARMUP_RUNS,
    benchmark_model,
)
from .devices import add_device, get_device, load_devices, remove_device
from .errors import MiraiRuntimeError
from .inspect import show_artifact_info, validate_artifact
from .package import (
    MIRAI_EXTENSION,
    create_mirai_package,
    validate_package_metadata,
)
from .pilot import (
    DEFAULT_PILOT_CONFIG,
    launch_artifact,
    load_pilot_config,
    run_pilot,
    write_pilot_template,
)
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

    pack_parser = subparsers.add_parser(
        "pack",
        help="cria um pacote .mirai reproduzível",
    )
    pack_parser.add_argument("model_path", type=Path, metavar="MODELO_ONNX")
    pack_parser.add_argument(
        "--name",
        required=True,
        metavar="NOME",
        help="identificador minúsculo do pacote",
    )
    pack_parser.add_argument(
        "--package-version",
        required=True,
        metavar="SEMVER",
        help="versão do modelo no formato SemVer (ex.: 1.0.0)",
    )
    pack_parser.add_argument(
        "--output",
        type=Path,
        metavar="ARQUIVO",
        help="destino .mirai (padrão: NOME-VERSÃO.mirai)",
    )
    pack_parser.add_argument(
        "--description",
        metavar="TEXTO",
        help="descrição curta incluída no manifesto",
    )
    pack_parser.add_argument(
        "--image-input",
        metavar="ENTRADA",
        help="entrada que receberá imagens",
    )
    pack_parser.add_argument(
        "--layout",
        choices=("auto", "nchw", "nhwc"),
        default="auto",
        help="layout da entrada de imagem (padrão: auto)",
    )
    pack_parser.add_argument(
        "--scale",
        type=float,
        metavar="NÚMERO",
        help="escala positiva dos pixels (padrão float: 1/255)",
    )
    pack_parser.add_argument(
        "--mean",
        metavar="JSON",
        help="média por canal, como [0.485, 0.456, 0.406]",
    )
    pack_parser.add_argument(
        "--std",
        metavar="JSON",
        help="desvio por canal, como [0.229, 0.224, 0.225]",
    )
    pack_parser.add_argument(
        "--replace",
        action="store_true",
        help="substitui o arquivo de saída quando ele já existe",
    )

    validate_parser = subparsers.add_parser(
        "validate",
        help="valida integralmente um modelo ONNX ou pacote .mirai",
    )
    validate_parser.add_argument("artifact_path", type=Path, metavar="ARQUIVO")

    info_parser = subparsers.add_parser(
        "info",
        help="inspeciona modelo ONNX ou manifesto .mirai",
    )
    info_parser.add_argument("artifact_path", type=Path, metavar="ARQUIVO")

    run_parser = subparsers.add_parser(
        "run",
        help="executa uma inferência local ou no Mirai Agent",
    )
    run_parser.add_argument(
        "model_path",
        type=Path,
        nargs="?",
        metavar="ARQUIVO",
        help="modelo local ou nome esperado do deployment ativo",
    )
    run_parser.add_argument(
        "--device",
        dest="device_name",
        metavar="NOME",
        help="executa no deployment ativo de um dispositivo cadastrado",
    )
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

    launch_parser = subparsers.add_parser(
        "launch",
        help="valida, implanta, ativa e testa um modelo no dispositivo",
    )
    launch_parser.add_argument("artifact_path", type=Path, metavar="ARQUIVO")
    launch_parser.add_argument(
        "--device",
        required=True,
        dest="device_name",
        metavar="NOME",
        help="dispositivo cadastrado com 'mirai device add'",
    )
    launch_parser.add_argument(
        "--no-run",
        action="store_true",
        help="encerra após ativar, sem executar a inferência de saúde",
    )
    _add_input_arguments(launch_parser)

    pilot_parser = subparsers.add_parser(
        "pilot",
        help="executa pilotos com critérios, benchmark, relatório e rollback",
    )
    pilot_subparsers = pilot_parser.add_subparsers(
        dest="pilot_command",
        title="ações de piloto",
        metavar="AÇÃO",
        required=True,
    )
    pilot_init_parser = pilot_subparsers.add_parser(
        "init",
        help="cria um arquivo declarativo mirai-pilot.json",
    )
    pilot_init_parser.add_argument(
        "config_path",
        type=Path,
        nargs="?",
        default=DEFAULT_PILOT_CONFIG,
        metavar="ARQUIVO",
    )
    pilot_init_parser.add_argument(
        "--replace",
        action="store_true",
        help="substitui uma configuração existente",
    )
    pilot_run_parser = pilot_subparsers.add_parser(
        "run",
        help="executa o projeto e gera evidências JSON e Markdown",
    )
    pilot_run_parser.add_argument(
        "config_path",
        type=Path,
        nargs="?",
        default=DEFAULT_PILOT_CONFIG,
        metavar="ARQUIVO",
    )

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

    device_pair_parser = device_subparsers.add_parser(
        "pair",
        help="pareia com segurança um Agent HTTPS",
    )
    device_pair_parser.add_argument("name", metavar="NOME")
    device_pair_parser.add_argument(
        "--url",
        required=True,
        metavar="URL",
        help="URL HTTPS exibida pelo Agent",
    )
    device_pair_parser.add_argument(
        "--code",
        required=True,
        metavar="CÓDIGO",
        help="código de uso único exibido no terminal do Agent",
    )
    device_pair_parser.add_argument(
        "--fingerprint",
        required=True,
        metavar="SHA256",
        help="fingerprint TLS exibido no terminal do Agent",
    )
    device_pair_parser.add_argument(
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

    device_revoke_parser = device_subparsers.add_parser(
        "revoke",
        help="revoga as credenciais e remove um dispositivo pareado",
    )
    device_revoke_parser.add_argument("name", metavar="NOME")

    deploy_parser = subparsers.add_parser(
        "deploy",
        help="valida e envia um ONNX ou pacote .mirai",
    )
    deploy_parser.add_argument("artifact_path", type=Path, metavar="ARQUIVO")
    deploy_parser.add_argument(
        "--device",
        required=True,
        dest="device_name",
        metavar="NOME",
        help="dispositivo cadastrado com 'mirai device add'",
    )

    activate_parser = subparsers.add_parser(
        "activate",
        help="ativa um deployment validado no dispositivo",
    )
    activate_parser.add_argument("deployment_id", metavar="DEPLOYMENT")
    activate_parser.add_argument(
        "--device",
        required=True,
        dest="device_name",
        metavar="NOME",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="exibe deployments e o modelo ativo de um dispositivo",
    )
    status_parser.add_argument(
        "--device",
        required=True,
        dest="device_name",
        metavar="NOME",
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

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="diagnostica conexão, segurança e runtime de um dispositivo",
    )
    doctor_parser.add_argument(
        "--device",
        required=True,
        dest="device_name",
        metavar="NOME",
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
    agent_start_parser.add_argument(
        "--secure",
        action="store_true",
        help="ativa HTTPS e pareamento mesmo em localhost",
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

        if args.command == "pack":
            validate_package_metadata(
                args.name,
                args.package_version,
                args.description,
            )
            output_path = args.output or Path(
                f"{args.name}-{args.package_version}{MIRAI_EXTENSION}"
            )
            package = create_mirai_package(
                args.model_path,
                output_path,
                name=args.name,
                package_version=args.package_version,
                description=args.description,
                image_input=args.image_input,
                layout=args.layout,
                scale=args.scale,
                mean=args.mean,
                std=args.std,
                replace=args.replace,
            )
            print(
                f"[MiraiOS] Pacote criado: {package.path} "
                f"({package.name} v{package.version})"
            )
            print(f"[MiraiOS] SHA-256: {package.sha256}")
            print(
                "[MiraiOS] Modelo: "
                f"{package.model_name} "
                f"({package.manifest['model']['sha256']})"
            )
            return 0

        if args.command == "validate":
            size_kb = validate_artifact(args.artifact_path)
            artifact_type = (
                "Pacote .mirai"
                if args.artifact_path.suffix.lower() == MIRAI_EXTENSION
                else "Modelo ONNX"
            )
            print(
                f"[MiraiOS] {artifact_type} válido: "
                f"{args.artifact_path.name} ({size_kb:.2f} KB)"
            )
            return 0

        if args.command == "info":
            show_artifact_info(args.artifact_path)
            return 0

        if args.command == "run":
            if args.device_name:
                device = get_device(args.device_name)
                model_name = (
                    args.model_path.name if args.model_path is not None else None
                )
                print(
                    f"[MiraiOS] Executando no dispositivo: {device.name}"
                )
                inference = run_remote_model(
                    device,
                    args.input_specs,
                    args.layout,
                    model_name,
                )
                print(
                    "[MiraiOS] Deployment: "
                    f"{inference['deployment_id']}"
                )
                print(
                    "[MiraiOS] Resultado da inferência: "
                    f"{inference['result']}"
                )
                print(
                    "[MiraiOS] Latência remota: "
                    f"{inference['latency_ms']:.2f} ms"
                )
                print(
                    "[MiraiOS] Tempo total no Agent: "
                    f"{inference['total_ms']:.2f} ms"
                )
                return 0

            if args.model_path is None:
                raise MiraiRuntimeError(
                    "informe ARQUIVO para execução local ou use --device"
                )
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

        if args.command == "launch":
            print(
                f"[MiraiOS] Launch: {args.artifact_path.name} → "
                f"{args.device_name}"
            )
            result = launch_artifact(
                args.artifact_path,
                args.device_name,
                args.input_specs,
                args.layout,
                run_inference=not args.no_run,
            )
            print(
                "[MiraiOS] Deployment ativo: "
                f"{result.deployment['deployment_id']}"
            )
            if result.inference is not None:
                print(
                    "[MiraiOS] Resultado da inferência: "
                    f"{result.inference['result']}"
                )
                print(
                    "[MiraiOS] Latência remota: "
                    f"{result.inference['latency_ms']:.2f} ms"
                )
            print("[MiraiOS] Launch concluído com sucesso.")
            return 0

        if args.command == "pilot":
            if args.pilot_command == "init":
                target = write_pilot_template(
                    args.config_path,
                    replace=args.replace,
                )
                print(f"[MiraiOS] Projeto de piloto criado: {target}")
                print(
                    "[MiraiOS] Próximo passo: edite o arquivo e execute "
                    f"'mirai pilot run {target.name}'."
                )
                return 0

            config = load_pilot_config(args.config_path)
            print(f"[MiraiOS] Pilot: iniciando {config.name}...")
            outcome = run_pilot(config)
            print(f"[MiraiOS] Evidência JSON: {outcome.report_json}")
            print(f"[MiraiOS] Relatório Markdown: {outcome.report_markdown}")
            if outcome.success:
                print("[MiraiOS] Piloto aprovado.")
                return 0
            return print_error(
                "piloto reprovado; consulte o relatório e o rollback"
            )

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

            if args.device_command == "pair":
                device, pairing = pair_device(
                    args.name,
                    args.url,
                    args.code,
                    args.fingerprint,
                    replace=args.replace,
                )
                print(
                    f"[MiraiOS] Dispositivo pareado: "
                    f"{device.name} ({device.url})"
                )
                print(f"[MiraiOS] Agent ID: {pairing['agent_id']}")
                print(
                    "[MiraiOS] Fingerprint TLS confirmado: "
                    f"{pairing['fingerprint']}"
                )
                return 0

            if args.device_command == "list":
                devices = load_devices()
                if not devices:
                    print("[MiraiOS] Nenhum dispositivo cadastrado.")
                    return 0
                print("[MiraiOS] Dispositivos cadastrados:")
                for device in devices.values():
                    mode = "pareado" if device.paired else "local"
                    print(f"- {device.name}: {device.url} ({mode})")
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

            if args.device_command == "revoke":
                device = get_device(args.name)
                revoked = revoke_remote_device(device)
                remove_device(args.name)
                print(
                    f"[MiraiOS] Credenciais revogadas: "
                    f"{device.name} ({revoked['client_id']})"
                )
                return 0

        if args.command == "deploy":
            device = get_device(args.device_name)
            print(
                f"[MiraiOS] Enviando {args.artifact_path.name} "
                f"para {device.name}..."
            )
            deployment = deploy_model(device, args.artifact_path)
            print(
                "[MiraiOS] Deployment pronto: "
                f"{deployment['deployment_id']}"
            )
            print(f"[MiraiOS] SHA-256: {deployment['sha256']}")
            package = deployment.get("package")
            if isinstance(package, dict):
                print(
                    "[MiraiOS] Pacote: "
                    f"{package.get('name')} v{package.get('version')}"
                )
            providers = deployment.get("providers") or []
            if providers:
                print(f"[MiraiOS] Providers: {', '.join(providers)}")
            return 0

        if args.command == "activate":
            device = get_device(args.device_name)
            deployment = activate_deployment(
                device,
                args.deployment_id,
            )
            print(
                "[MiraiOS] Deployment ativo: "
                f"{deployment['deployment_id']} ({deployment['model']})"
            )
            return 0

        if args.command == "status":
            device = get_device(args.device_name)
            status = get_deployment_status(device)
            deployments = status["deployments"]
            active_id = status.get("active_deployment_id")
            print(f"[MiraiOS] Status do dispositivo: {device.name}")
            if not deployments:
                print("[MiraiOS] Nenhum deployment encontrado.")
                return 0
            for deployment in deployments:
                marker = "●" if deployment["deployment_id"] == active_id else "○"
                providers = ", ".join(deployment.get("providers") or [])
                provider_suffix = f" | {providers}" if providers else ""
                package = deployment.get("package")
                package_suffix = (
                    f" | {package.get('name')} v{package.get('version')}"
                    if isinstance(package, dict)
                    else ""
                )
                print(
                    f"{marker} {deployment['deployment_id']} | "
                    f"{deployment['model']} | {deployment['status']}"
                    f"{package_suffix}{provider_suffix}"
                )
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

        if args.command == "doctor":
            device = get_device(args.device_name)
            report = doctor_device(device)
            health = report["health"]
            info = report["info"]
            deployments = report["deployments"]
            connection_mode = (
                "HTTPS com fingerprint fixado"
                if report["tls"]
                else "HTTP local"
            )
            authentication = (
                "token pareado"
                if report["authenticated"]
                else "dispensada em localhost"
            )
            compatibility = (
                "compatível" if report["compatible"] else "incompatível"
            )
            providers = ", ".join(info.get("providers") or []) or "nenhum"
            print(f"[MiraiOS] Doctor: {device.name}")
            print(f"✓ Conexão: {health.get('status', 'desconhecida')}")
            print(f"✓ Canal: {connection_mode}")
            print(f"✓ Autenticação: {authentication}")
            print(
                "✓ Versões: "
                f"CLI {__version__} / Agent "
                f"{health.get('agent_version', 'desconhecida')} "
                f"({compatibility})"
            )
            print(f"✓ Runtime: {providers}")
            print(
                "✓ Deployments: "
                f"{len(deployments.get('deployments') or [])}"
            )
            active_id = deployments.get("active_deployment_id")
            print(f"✓ Ativo: {active_id or 'nenhum'}")
            if not report["compatible"]:
                raise MiraiRuntimeError(
                    "as versões da CLI e do Agent não são compatíveis"
                )
            return 0

        if args.command == "agent" and args.agent_command == "start":
            run_agent(
                args.host,
                args.port,
                args.data_dir,
                force_secure=args.secure,
            )
            return 0
    except MiraiRuntimeError as error:
        return print_error(str(error))

    parser.print_help()
    return 0
