"""Interface de linha de comando do MiraiOS."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .agent import run_agent
from .agent_client import (
    activate_deployment,
    delete_deployment,
    deploy_model,
    deployment_retention_candidates,
    doctor_device,
    get_agent_clients,
    get_agent_info,
    get_agent_logs,
    get_deployment_status,
    pair_device,
    revoke_remote_device,
    run_remote_model,
    set_agent_client_role,
)
from .anchors import DEFAULT_ANCHOR_PATH, anchor_device, anchor_fleet
from .benchmark import (
    DEFAULT_BENCHMARK_RUNS,
    DEFAULT_WARMUP_RUNS,
    benchmark_model,
)
from .devices import (
    add_device,
    get_device,
    load_devices,
    mirai_home,
    remove_device,
    update_device_tags,
)
from .discovery import discover_agents
from .errors import MiraiRuntimeError
from .fit import fit_model
from .fleet import (
    DEFAULT_ROLLOUT_DIRECTORY,
    execute_rollout,
    inspect_fleet,
    observe_fleet,
    select_devices,
)
from .history import (
    get_pilot_report,
    list_pilot_history,
    prune_pilot_history,
)
from .inspect import show_artifact_info, validate_artifact
from .package import (
    MIRAI_EXTENSION,
    create_mirai_package,
    validate_package_metadata,
)
from .pilot import (
    DEFAULT_PILOT_CONFIG,
    DEFAULT_REPORT_DIRECTORY,
    launch_artifact,
    load_pilot_config,
    run_pilot,
    write_pilot_template,
)
from .providers import PROVIDER_PROFILES, list_runtime_backends
from .runtime import run_model
from .security import ACCESS_ROLES, rotate_agent_identity
from .signing import (
    generate_signing_key,
    sign_artifact,
    signing_key_paths,
    verify_artifact,
)

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


def unit_interval(value: str) -> float:
    """Converte um argumento em proporção finita entre zero e um."""
    try:
        parsed_value = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("deve ser um número") from error
    if not math.isfinite(parsed_value) or not 0.0 <= parsed_value <= 1.0:
        raise argparse.ArgumentTypeError("deve estar entre 0 e 1")
    return parsed_value


def non_negative_float(value: str) -> float:
    """Converte um argumento em número finito não negativo."""
    try:
        parsed_value = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("deve ser um número") from error
    if not math.isfinite(parsed_value) or parsed_value < 0:
        raise argparse.ArgumentTypeError("deve ser finito e não negativo")
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

    key_parser = subparsers.add_parser(
        "key",
        help="gerencia chaves Ed25519 para assinaturas",
    )
    key_subparsers = key_parser.add_subparsers(
        dest="key_command",
        metavar="AÇÃO",
        required=True,
    )
    key_generate_parser = key_subparsers.add_parser(
        "generate",
        help="gera um par de chaves Ed25519 local",
    )
    key_generate_parser.add_argument("name", metavar="NOME")
    key_generate_parser.add_argument(
        "--replace",
        action="store_true",
        help="substitui um par existente",
    )

    sign_parser = subparsers.add_parser(
        "sign",
        help="assina um pacote .mirai ou relatório JSON com DSSE/Ed25519",
    )
    sign_parser.add_argument("artifact_path", type=Path, metavar="ARTEFATO")
    sign_parser.add_argument("--key", required=True, type=Path, metavar="CHAVE_PRIVADA")
    sign_parser.add_argument("--output", type=Path, metavar="ASSINATURA")
    sign_parser.add_argument("--replace", action="store_true")

    verify_parser = subparsers.add_parser(
        "verify",
        help="verifica assinatura, chave e digest do artefato",
    )
    verify_parser.add_argument("artifact_path", type=Path, metavar="ARTEFATO")
    verify_parser.add_argument("--signature", required=True, type=Path)
    verify_parser.add_argument("--key", required=True, type=Path, metavar="CHAVE_PÚBLICA")

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

    fit_parser = subparsers.add_parser(
        "fit",
        help="gera e aprova uma variante INT8 com benchmark e gate de qualidade",
    )
    fit_parser.add_argument("model_path", type=Path, metavar="MODELO_ONNX")
    fit_parser.add_argument("--name", required=True, metavar="NOME")
    fit_parser.add_argument(
        "--package-version",
        required=True,
        metavar="SEMVER",
    )
    fit_parser.add_argument("--output", required=True, type=Path, metavar="PACOTE.mirai")
    fit_parser.add_argument("--runs", type=positive_int, default=50)
    fit_parser.add_argument("--warmup", type=non_negative_int, default=3)
    fit_parser.add_argument(
        "--max-absolute-error",
        type=non_negative_float,
        default=0.05,
    )
    fit_parser.add_argument(
        "--min-speedup",
        type=non_negative_float,
        default=1.0,
        help="speedup P95 mínimo; use 0 para avaliar sem gate de desempenho",
    )
    fit_parser.add_argument("--per-channel", action="store_true")
    fit_parser.add_argument("--sign-key", type=Path, metavar="CHAVE_PRIVADA")
    fit_parser.add_argument("--replace", action="store_true")
    _add_input_arguments(fit_parser)

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
        "--provider-profile",
        choices=tuple(PROVIDER_PROFILES),
        default="auto",
        help="provider: auto, cpu, cuda ou directml",
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
    pilot_history_parser = pilot_subparsers.add_parser(
        "history",
        help="consulta execuções anteriores",
    )
    pilot_history_parser.add_argument(
        "--directory",
        type=Path,
        default=DEFAULT_REPORT_DIRECTORY,
    )
    pilot_history_parser.add_argument("--limit", type=positive_int, default=20)
    pilot_history_parser.add_argument("--status", choices=("passed", "failed"))
    pilot_show_parser = pilot_subparsers.add_parser(
        "show",
        help="mostra o JSON de um piloto pelo run_id",
    )
    pilot_show_parser.add_argument("run_id")
    pilot_show_parser.add_argument(
        "--directory",
        type=Path,
        default=DEFAULT_REPORT_DIRECTORY,
    )
    pilot_prune_parser = pilot_subparsers.add_parser(
        "prune",
        help="aplica retenção aos relatórios; padrão é simulação",
    )
    pilot_prune_parser.add_argument("--directory", type=Path, default=DEFAULT_REPORT_DIRECTORY)
    pilot_prune_parser.add_argument("--keep", type=non_negative_int, required=True)
    pilot_prune_parser.add_argument("--apply", action="store_true")

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

    device_clients_parser = device_subparsers.add_parser(
        "clients",
        help="lista clientes pareados (requer admin)",
    )
    device_clients_parser.add_argument("name", metavar="NOME")
    device_role_parser = device_subparsers.add_parser(
        "role",
        help="altera o papel de um cliente (requer admin)",
    )
    device_role_parser.add_argument("name", metavar="NOME")
    device_role_parser.add_argument("client_id", metavar="CLIENTE")
    device_role_parser.add_argument("role", choices=ACCESS_ROLES)
    device_tag_parser = device_subparsers.add_parser(
        "tag",
        help="define ou remove tags usadas pelo control plane",
    )
    device_tag_parser.add_argument("name", metavar="NOME")
    device_tag_parser.add_argument(
        "--set",
        dest="set_tags",
        action="append",
        default=[],
        metavar="CHAVE=VALOR",
    )
    device_tag_parser.add_argument(
        "--remove",
        dest="remove_tags",
        action="append",
        default=[],
        metavar="CHAVE",
    )
    device_discover_parser = device_subparsers.add_parser(
        "discover",
        help="encontra candidatos mDNS sem confiar ou cadastrá-los",
    )
    device_discover_parser.add_argument("--timeout", type=float, default=2.0)

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
    deploy_parser.add_argument(
        "--provider-profile",
        choices=tuple(PROVIDER_PROFILES),
        default="auto",
    )
    deploy_parser.add_argument(
        "--signature",
        type=Path,
        metavar="ARQUIVO.sig",
        help="envelope DSSE usado por Agents com admissão signed",
    )

    cleanup_parser = subparsers.add_parser(
        "cleanup",
        help="aplica retenção a deployments inativos; padrão é simulação",
    )
    cleanup_parser.add_argument("--device", required=True, dest="device_name")
    cleanup_parser.add_argument("--keep", type=non_negative_int, required=True)
    cleanup_parser.add_argument("--apply", action="store_true")

    fleet_parser = subparsers.add_parser("fleet", help="consulta toda a frota cadastrada")
    fleet_subparsers = fleet_parser.add_subparsers(
        dest="fleet_command", metavar="AÇÃO", required=True
    )
    fleet_status_parser = fleet_subparsers.add_parser(
        "status", help="mostra saúde, hardware e deployment ativo"
    )
    fleet_status_parser.add_argument("--selector", metavar="CHAVE=VALOR")
    fleet_observe_parser = fleet_subparsers.add_parser(
        "observe", help="coleta métricas e sinais heurísticos de drift"
    )
    fleet_observe_parser.add_argument("--selector", metavar="CHAVE=VALOR")
    fleet_observe_parser.add_argument("--workers", type=positive_int, default=8)
    fleet_anchor_parser = fleet_subparsers.add_parser(
        "anchor", help="ancora externamente os heads da frota"
    )
    fleet_anchor_parser.add_argument("--selector", metavar="CHAVE=VALOR")
    fleet_anchor_parser.add_argument("--workers", type=positive_int, default=4)
    fleet_anchor_parser.add_argument(
        "--ledger",
        type=Path,
        default=DEFAULT_ANCHOR_PATH,
    )
    fleet_rollout_parser = fleet_subparsers.add_parser(
        "rollout", help="planeja ou executa um rollout canário com rollback"
    )
    fleet_rollout_parser.add_argument("artifact_path", type=Path, metavar="ARQUIVO")
    fleet_rollout_parser.add_argument("--selector", metavar="CHAVE=VALOR")
    fleet_rollout_parser.add_argument("--canary", type=positive_int, default=10)
    fleet_rollout_parser.add_argument("--batch-size", type=positive_int, default=10)
    fleet_rollout_parser.add_argument("--max-failure-rate", type=unit_interval, default=0.0)
    fleet_rollout_parser.add_argument("--workers", type=positive_int, default=4)
    fleet_rollout_parser.add_argument(
        "--provider-profile", choices=tuple(PROVIDER_PROFILES), default="auto"
    )
    fleet_rollout_parser.add_argument("--signature", type=Path)
    fleet_rollout_parser.add_argument(
        "--report-directory", type=Path, default=DEFAULT_ROLLOUT_DIRECTORY
    )
    fleet_rollout_parser.add_argument(
        "--apply",
        action="store_true",
        help="executa; sem esta opção apenas grava o plano",
    )
    _add_input_arguments(fleet_rollout_parser)

    audit_parser = subparsers.add_parser(
        "audit", help="verifica e ancora auditoria fora do dispositivo"
    )
    audit_subparsers = audit_parser.add_subparsers(
        dest="audit_command", metavar="AÇÃO", required=True
    )
    audit_anchor_parser = audit_subparsers.add_parser(
        "anchor", help="ancora o head de um dispositivo no control plane"
    )
    audit_anchor_parser.add_argument("--device", required=True, dest="device_name")
    audit_anchor_parser.add_argument("--ledger", type=Path, default=DEFAULT_ANCHOR_PATH)

    runtime_parser = subparsers.add_parser("runtime", help="inspeciona backends de runtime")
    runtime_subparsers = runtime_parser.add_subparsers(
        dest="runtime_command", metavar="AÇÃO", required=True
    )
    runtime_subparsers.add_parser("list", help="lista backend interno e plugins experimentais")

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
    agent_start_parser.add_argument(
        "--pairing-role",
        choices=ACCESS_ROLES,
        default="admin",
        help="papel concedido pelo código de pareamento atual",
    )
    agent_start_parser.add_argument(
        "--discoverable",
        action="store_true",
        help="anuncia o Agent via mDNS (não substitui o pareamento)",
    )
    agent_start_parser.add_argument(
        "--admission",
        choices=("open", "signed"),
        default="open",
        help="signed aceita somente pacotes .mirai assinados",
    )
    agent_start_parser.add_argument(
        "--trust-key",
        action="append",
        type=Path,
        default=[],
        metavar="CHAVE.pub",
        help="chave Ed25519 confiável; a opção pode ser repetida",
    )
    agent_rotate_parser = agent_subparsers.add_parser(
        "rotate-identity",
        help="troca certificado e invalida todos os clientes pareados",
    )
    agent_rotate_parser.add_argument("--data-dir", type=Path, default=Path(".mirai-agent"))
    agent_rotate_parser.add_argument("--confirm", required=True, metavar="AGENT_ID_ATUAL")
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
            created_package = create_mirai_package(
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
                f"[MiraiOS] Pacote criado: {created_package.path} "
                f"({created_package.name} v{created_package.version})"
            )
            print(f"[MiraiOS] SHA-256: {created_package.sha256}")
            print(
                "[MiraiOS] Modelo: "
                f"{created_package.model_name} "
                f"({created_package.manifest['model']['sha256']})"
            )
            return 0

        if args.command == "key" and args.key_command == "generate":
            private_path, public_path = signing_key_paths(mirai_home(), args.name)
            generated = generate_signing_key(
                private_path,
                public_path,
                replace=args.replace,
            )
            print(f"[MiraiOS] Chave privada: {generated['private_key']}")
            print(f"[MiraiOS] Chave pública: {generated['public_key']}")
            print(f"[MiraiOS] Key ID: {generated['key_id']}")
            return 0

        if args.command == "sign":
            signed_artifact = sign_artifact(
                args.artifact_path,
                args.key,
                args.output,
                replace=args.replace,
            )
            print(f"[MiraiOS] Assinatura DSSE: {signed_artifact['signature']}")
            print(f"[MiraiOS] Key ID: {signed_artifact['key_id']}")
            print(f"[MiraiOS] SHA-256: {signed_artifact['payload']['sha256']}")
            return 0

        if args.command == "verify":
            verified = verify_artifact(
                args.artifact_path,
                args.signature,
                args.key,
            )
            print("[MiraiOS] Assinatura válida.")
            print(f"[MiraiOS] Key ID: {verified['key_id']}")
            print(f"[MiraiOS] SHA-256: {verified['payload']['sha256']}")
            return 0

        if args.command == "validate":
            size_kb = validate_artifact(args.artifact_path)
            artifact_type = (
                "Pacote .mirai"
                if args.artifact_path.suffix.lower() == MIRAI_EXTENSION
                else "Modelo ONNX"
            )
            print(f"[MiraiOS] {artifact_type} válido: {args.artifact_path.name} ({size_kb:.2f} KB)")
            return 0

        if args.command == "info":
            show_artifact_info(args.artifact_path)
            return 0

        if args.command == "run":
            if args.device_name:
                device = get_device(args.device_name)
                model_name = args.model_path.name if args.model_path is not None else None
                print(f"[MiraiOS] Executando no dispositivo: {device.name}")
                inference = run_remote_model(
                    device,
                    args.input_specs,
                    args.layout,
                    model_name,
                )
                print(f"[MiraiOS] Deployment: {inference['deployment_id']}")
                print(f"[MiraiOS] Resultado da inferência: {inference['result']}")
                print(f"[MiraiOS] Latência remota: {inference['latency_ms']:.2f} ms")
                print(f"[MiraiOS] Tempo total no Agent: {inference['total_ms']:.2f} ms")
                return 0

            if args.model_path is None:
                raise MiraiRuntimeError("informe ARQUIVO para execução local ou use --device")
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
            print(f"[MiraiOS] Inferências por segundo (IPS): {stats.inferences_per_second:.2f}")
            return 0

        if args.command == "fit":
            print(f"[MiraiOS] Fit: avaliando {args.model_path.name}...")
            fit_outcome = fit_model(
                args.model_path,
                args.output,
                name=args.name,
                package_version=args.package_version,
                input_specs=args.input_specs,
                layout=args.layout,
                runs=args.runs,
                warmup_runs=args.warmup,
                max_absolute_error=args.max_absolute_error,
                min_speedup=args.min_speedup,
                per_channel=args.per_channel,
                signing_key_path=args.sign_key,
                replace=args.replace,
            )
            print(f"[MiraiOS] Evidência Fit: {fit_outcome.report_path}")
            benchmark = fit_outcome.report["benchmark"]
            quality = fit_outcome.report["quality"]
            print(f"[MiraiOS] Speedup P95: {benchmark['p95_speedup']:.3f}x")
            print(f"[MiraiOS] Erro absoluto máximo: {quality['max_absolute_error']:.8f}")
            if not fit_outcome.accepted:
                return print_error("variante rejeitada pelos gates; o pacote não foi publicado")
            print(f"[MiraiOS] Variante aprovada: {fit_outcome.package_path}")
            if fit_outcome.signature_path is not None:
                print(f"[MiraiOS] Assinatura DSSE: {fit_outcome.signature_path}")
            return 0

        if args.command == "launch":
            print(f"[MiraiOS] Launch: {args.artifact_path.name} → {args.device_name}")
            result = launch_artifact(
                args.artifact_path,
                args.device_name,
                args.input_specs,
                args.layout,
                run_inference=not args.no_run,
                provider_profile=args.provider_profile,
            )
            print(f"[MiraiOS] Deployment ativo: {result.deployment['deployment_id']}")
            if result.inference is not None:
                print(f"[MiraiOS] Resultado da inferência: {result.inference['result']}")
                print(f"[MiraiOS] Latência remota: {result.inference['latency_ms']:.2f} ms")
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

            if args.pilot_command == "history":
                pilot_entries = list_pilot_history(
                    args.directory,
                    limit=args.limit,
                    status=args.status,
                )
                if not pilot_entries:
                    print("[MiraiOS] Nenhum piloto encontrado.")
                    return 0
                print("[MiraiOS] Histórico de pilotos:")
                for pilot_entry in pilot_entries:
                    signature_status = (
                        "assinado" if Path(pilot_entry["signature"]).is_file() else "sem assinatura"
                    )
                    print(
                        f"- {pilot_entry['run_id']} | {pilot_entry['project']} | "
                        f"{pilot_entry['status']} | {pilot_entry.get('device') or '-'} | "
                        f"{signature_status}"
                    )
                return 0

            if args.pilot_command == "show":
                report = get_pilot_report(args.directory, args.run_id)
                print(json.dumps(report, indent=2, ensure_ascii=False))
                return 0

            if args.pilot_command == "prune":
                pilot_candidates = prune_pilot_history(
                    args.directory,
                    keep=args.keep,
                    apply=args.apply,
                )
                action = "Removidos" if args.apply else "Seriam removidos"
                print(f"[MiraiOS] {action}: {len(pilot_candidates)} arquivo(s).")
                for pilot_candidate in pilot_candidates:
                    print(f"- {pilot_candidate}")
                if not args.apply and pilot_candidates:
                    print("[MiraiOS] Revise e repita com --apply para confirmar.")
                return 0

            config = load_pilot_config(args.config_path)
            print(f"[MiraiOS] Pilot: iniciando {config.name}...")
            outcome = run_pilot(config)
            print(f"[MiraiOS] Evidência JSON: {outcome.report_json}")
            print(f"[MiraiOS] Relatório Markdown: {outcome.report_markdown}")
            if outcome.report_signature is not None:
                print(f"[MiraiOS] Assinatura DSSE: {outcome.report_signature}")
            if outcome.success:
                print("[MiraiOS] Piloto aprovado.")
                return 0
            return print_error("piloto reprovado; consulte o relatório e o rollback")

        if args.command == "device":
            if args.device_command == "add":
                device = add_device(
                    args.name,
                    args.url,
                    replace=args.replace,
                )
                print(f"[MiraiOS] Dispositivo cadastrado: {device.name} ({device.url})")
                return 0

            if args.device_command == "pair":
                device, pairing = pair_device(
                    args.name,
                    args.url,
                    args.code,
                    args.fingerprint,
                    replace=args.replace,
                )
                print(f"[MiraiOS] Dispositivo pareado: {device.name} ({device.url})")
                print(f"[MiraiOS] Agent ID: {pairing['agent_id']}")
                print(f"[MiraiOS] Fingerprint TLS confirmado: {pairing['fingerprint']}")
                return 0

            if args.device_command == "list":
                devices = load_devices()
                if not devices:
                    print("[MiraiOS] Nenhum dispositivo cadastrado.")
                    return 0
                print("[MiraiOS] Dispositivos cadastrados:")
                for device in devices.values():
                    mode = "pareado" if device.paired else "local"
                    tags = f" | {', '.join(device.tags)}" if device.tags else ""
                    print(f"- {device.name}: {device.url} ({mode}){tags}")
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
                print(f"[MiraiOS] Arquitetura: {info.get('machine', 'desconhecida')}")
                device_providers = info.get("providers") or []
                provider_text = ", ".join(device_providers) if device_providers else "nenhum"
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
                print(f"[MiraiOS] Credenciais revogadas: {device.name} ({revoked['client_id']})")
                return 0

            if args.device_command == "clients":
                device = get_device(args.name)
                clients = get_agent_clients(device)
                if not clients:
                    print("[MiraiOS] Nenhum cliente pareado.")
                    return 0
                print(f"[MiraiOS] Clientes de {device.name}:")
                for client in clients:
                    print(
                        f"- {client['client_id']} | {client['name']} | "
                        f"{client.get('role', 'viewer')} | {client.get('last_seen_at')}"
                    )
                return 0

            if args.device_command == "role":
                device = get_device(args.name)
                client = set_agent_client_role(
                    device,
                    args.client_id,
                    args.role,
                )
                print(f"[MiraiOS] Papel atualizado: {client['client_id']} → {client['role']}")
                return 0

            if args.device_command == "tag":
                device = update_device_tags(
                    args.name,
                    set_tags=args.set_tags,
                    remove_keys=args.remove_tags,
                )
                tags = ", ".join(device.tags) if device.tags else "nenhuma"
                print(f"[MiraiOS] Tags de {device.name}: {tags}")
                return 0

            if args.device_command == "discover":
                discovered_candidates = discover_agents(args.timeout)
                if not discovered_candidates:
                    print("[MiraiOS] Nenhum candidato mDNS encontrado.")
                    return 0
                print("[MiraiOS] Candidatos não confiáveis (pareamento obrigatório):")
                for discovered_candidate in discovered_candidates:
                    print(
                        f"- {discovered_candidate.name}: {discovered_candidate.url} | "
                        f"Agent ID {discovered_candidate.agent_id or 'não informado'}"
                    )
                return 0

        if args.command == "deploy":
            device = get_device(args.device_name)
            print(f"[MiraiOS] Enviando {args.artifact_path.name} para {device.name}...")
            deployment = deploy_model(
                device,
                args.artifact_path,
                args.provider_profile,
                args.signature,
            )
            print(f"[MiraiOS] Deployment pronto: {deployment['deployment_id']}")
            print(f"[MiraiOS] SHA-256: {deployment['sha256']}")
            deployment_package = deployment.get("package")
            if isinstance(deployment_package, dict):
                print(
                    "[MiraiOS] Pacote: "
                    f"{deployment_package.get('name')} v{deployment_package.get('version')}"
                )
            deployment_providers = deployment.get("providers") or []
            if deployment_providers:
                print(f"[MiraiOS] Providers: {', '.join(deployment_providers)}")
            return 0

        if args.command == "cleanup":
            device = get_device(args.device_name)
            status = get_deployment_status(device)
            retention_candidates = deployment_retention_candidates(status, args.keep)
            action = "Removendo" if args.apply else "Simulação"
            print(f"[MiraiOS] {action}: {len(retention_candidates)} deployment(s).")
            for retention_deployment in retention_candidates:
                print(
                    f"- {retention_deployment['deployment_id']} | "
                    f"{retention_deployment.get('model')} | "
                    f"{retention_deployment.get('created_at')}"
                )
                if args.apply:
                    delete_deployment(device, str(retention_deployment["deployment_id"]))
            if retention_candidates and not args.apply:
                print("[MiraiOS] Revise e repita com --apply para confirmar.")
            return 0

        if args.command == "fleet" and args.fleet_command == "status":
            selected = select_devices(load_devices(), args.selector)
            results = inspect_fleet({device.name: device for device in selected})
            if not results:
                print("[MiraiOS] Nenhum dispositivo corresponde ao seletor.")
                return 0
            print("[MiraiOS] Visão da frota:")
            for item in results:
                if item["status"] == "offline":
                    print(f"○ {item['name']} | offline | {item['error']}")
                    continue
                hardware = item.get("hardware_profile") or {}
                profile = hardware.get("profile", "desconhecido")
                print(
                    f"● {item['name']} | {profile} | "
                    f"{item.get('active_deployment_id') or 'sem deployment'} | "
                    f"{item['deployment_count']} total"
                )
            return 0

        if args.command == "fleet" and args.fleet_command == "observe":
            selected = select_devices(load_devices(), args.selector)
            results = observe_fleet(selected, workers=args.workers)
            if not results:
                print("[MiraiOS] Nenhum dispositivo corresponde ao seletor.")
                return 0
            print("[MiraiOS] Observabilidade da frota:")
            for item in results:
                if item["status"] == "offline":
                    print(f"○ {item['name']} | offline | {item['error']}")
                    continue
                counters = item["metrics"].get("counters", {})
                warnings = sum(
                    signal.get("status") == "warning"
                    for deployment in item["drift"].get("deployments", {}).values()
                    for signal in (
                        deployment.get("latency", {}),
                        deployment.get("output", {}),
                    )
                )
                print(
                    f"● {item['name']} | "
                    f"{counters.get('inferences_total', 0)} inferências | "
                    f"{counters.get('inference_failures_total', 0)} falhas | "
                    f"{warnings} alerta(s) de drift"
                )
            return 0

        if args.command == "fleet" and args.fleet_command == "anchor":
            selected = select_devices(load_devices(), args.selector)
            results = anchor_fleet(
                selected,
                ledger_path=args.ledger,
                workers=args.workers,
            )
            if not results:
                print("[MiraiOS] Nenhum dispositivo corresponde ao seletor.")
                return 0
            failed = False
            print(f"[MiraiOS] Ledger externo: {args.ledger}")
            for item in results:
                marker = "✓" if item["status"] != "failed" else "✗"
                if item["status"] == "failed":
                    failed = True
                    print(f"{marker} {item['device']} | falha | {item['error']}")
                else:
                    print(
                        f"{marker} {item['device']} | {item['status']} | "
                        f"{item['records']} registro(s) | {item['head'][:12]}"
                    )
            return 1 if failed else 0

        if args.command == "fleet" and args.fleet_command == "rollout":
            report = execute_rollout(
                args.artifact_path,
                load_devices(),
                selector=args.selector,
                canary_percent=args.canary,
                batch_size=args.batch_size,
                max_failure_rate=args.max_failure_rate,
                workers=args.workers,
                input_specs=args.input_specs,
                layout=args.layout,
                provider_profile=args.provider_profile,
                signature_path=args.signature,
                apply=args.apply,
                report_directory=args.report_directory,
            )
            action = "Rollout" if args.apply else "Plano"
            print(f"[MiraiOS] {action}: {report['run_id']}")
            print(f"[MiraiOS] Status: {report['status']}")
            print(f"[MiraiOS] Dispositivos: {sum(len(batch) for batch in report['batches'])}")
            print(f"[MiraiOS] Evidência: {report['report_path']}")
            if report["status"] in {"rolled_back", "rollback_failed"}:
                return print_error("rollout interrompido; consulte a evidência")
            if not args.apply:
                print("[MiraiOS] Revise o plano e repita com --apply.")
            return 0

        if args.command == "audit" and args.audit_command == "anchor":
            device = get_device(args.device_name)
            result = anchor_device(device, ledger_path=args.ledger)
            print(f"[MiraiOS] Auditoria de {device.name}: {result['status']}")
            print(f"[MiraiOS] Registros: {result['records']}")
            print(f"[MiraiOS] Head: {result['head']}")
            print(f"[MiraiOS] Ledger externo: {args.ledger}")
            return 0

        if args.command == "runtime" and args.runtime_command == "list":
            print("[MiraiOS] Backends de runtime:")
            for backend in list_runtime_backends():
                print(f"- {backend['name']} | {backend['status']} | {backend['source']}")
            return 0

        if args.command == "activate":
            device = get_device(args.device_name)
            deployment = activate_deployment(
                device,
                args.deployment_id,
            )
            print(
                f"[MiraiOS] Deployment ativo: {deployment['deployment_id']} ({deployment['model']})"
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
                status_providers = ", ".join(deployment.get("providers") or [])
                provider_suffix = f" | {status_providers}" if status_providers else ""
                status_package = deployment.get("package")
                package_suffix = (
                    f" | {status_package.get('name')} v{status_package.get('version')}"
                    if isinstance(status_package, dict)
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
            audit = report["audit"]
            connection_mode = "HTTPS com fingerprint fixado" if report["tls"] else "HTTP local"
            authentication = (
                "token pareado" if report["authenticated"] else "dispensada em localhost"
            )
            compatibility = "compatível" if report["compatible"] else "incompatível"
            doctor_providers = ", ".join(info.get("providers") or []) or "nenhum"
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
            print(f"✓ Runtime: {doctor_providers}")
            print(f"✓ Deployments: {len(deployments.get('deployments') or [])}")
            active_id = deployments.get("active_deployment_id")
            print(f"✓ Ativo: {active_id or 'nenhum'}")
            print(
                "✓ Auditoria: "
                f"{audit.get('records', 0)} registro(s), "
                f"head {str(audit.get('head', ''))[:12]}"
            )
            if not report["compatible"]:
                raise MiraiRuntimeError("as versões da CLI e do Agent não são compatíveis")
            return 0

        if args.command == "agent" and args.agent_command == "start":
            run_agent(
                args.host,
                args.port,
                args.data_dir,
                force_secure=args.secure,
                pairing_role=args.pairing_role,
                discoverable=args.discoverable,
                admission_mode=args.admission,
                trusted_keys=tuple(args.trust_key),
            )
            return 0

        if args.command == "agent" and args.agent_command == "rotate-identity":
            identity = rotate_agent_identity(args.data_dir, args.confirm)
            print(f"[MiraiOS] Nova identidade: {identity.agent_id}")
            print(f"[MiraiOS] Novo fingerprint: {identity.fingerprint}")
            print("[MiraiOS] Todos os clientes antigos foram invalidados.")
            return 0
    except MiraiRuntimeError as error:
        return print_error(str(error))

    parser.print_help()
    return 0
