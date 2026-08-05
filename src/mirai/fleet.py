"""Control plane local, determinístico e tolerante a falhas da frota edge."""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_client import doctor_device, get_agent_drift, get_agent_metrics
from .devices import Device, device_tag_map, normalize_tag_key, normalize_tag_value
from .errors import MiraiRuntimeError
from .inspect import validate_artifact
from .json_codec import strict_json_dumps
from .pilot import LaunchResult, launch_artifact, rollback_launch
from .providers import normalize_provider_profile
from .storage import atomic_write_text

MAX_FLEET_WORKERS = 32
MAX_SELECTOR_TERMS = 16
DEFAULT_ROLLOUT_DIRECTORY = Path(".mirai") / "rollouts"


def _inspect_device(
    device: Device,
    doctor: Callable[[Device], dict[str, Any]],
) -> dict[str, Any]:
    try:
        report = doctor(device)
        info = report["info"]
        deployments = report["deployments"]
        return {
            "name": device.name,
            "url": device.url,
            "status": "online",
            "compatible": bool(report.get("compatible")),
            "machine": info.get("machine"),
            "hardware_profile": info.get("hardware_profile"),
            "providers": info.get("providers") or [],
            "active_deployment_id": deployments.get("active_deployment_id"),
            "deployment_count": len(deployments.get("deployments") or []),
            "error": None,
        }
    except Exception as error:  # noqa: BLE001 - uma falha não derruba a visão da frota.
        return {
            "name": device.name,
            "url": device.url,
            "status": "offline",
            "compatible": False,
            "machine": None,
            "hardware_profile": None,
            "providers": [],
            "active_deployment_id": None,
            "deployment_count": 0,
            "error": str(error),
        }


def inspect_fleet(
    devices: dict[str, Device],
    *,
    workers: int = 8,
    doctor: Callable[[Device], dict[str, Any]] = doctor_device,
) -> list[dict[str, Any]]:
    """Consulta vários Agents em paralelo sem abortar por um host offline."""
    if not devices:
        return []
    worker_count = max(1, min(workers, 32, len(devices)))
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(_inspect_device, device, doctor): device.name
            for device in devices.values()
        }
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item["name"])
    return results


def observe_fleet(
    devices: list[Device],
    *,
    workers: int = 8,
    metrics: Callable[[Device], dict[str, Any]] = get_agent_metrics,
    drift: Callable[[Device], dict[str, Any]] = get_agent_drift,
) -> list[dict[str, Any]]:
    """Coleta métricas e drift em paralelo sem esconder dispositivos offline."""
    if not 1 <= workers <= MAX_FLEET_WORKERS:
        raise MiraiRuntimeError(f"workers deve estar entre 1 e {MAX_FLEET_WORKERS}")

    def observe(device: Device) -> dict[str, Any]:
        try:
            return {
                "name": device.name,
                "status": "online",
                "metrics": metrics(device),
                "drift": drift(device),
                "error": None,
            }
        except Exception as error:  # noqa: BLE001 - falha parcial é resultado.
            return {
                "name": device.name,
                "status": "offline",
                "metrics": None,
                "drift": None,
                "error": str(error),
            }

    if not devices:
        return []
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(workers, len(devices))) as executor:
        futures = [executor.submit(observe, device) for device in devices]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item["name"])
    return results


def parse_selector(selector: str | None) -> dict[str, str]:
    """Valida um seletor conjuntivo no formato ``chave=valor,chave=valor``."""
    if selector is None or not selector.strip():
        return {}
    terms = selector.split(",")
    if len(terms) > MAX_SELECTOR_TERMS:
        raise MiraiRuntimeError(f"seletor aceita no máximo {MAX_SELECTOR_TERMS} condições")
    parsed: dict[str, str] = {}
    for term in terms:
        key, separator, value = term.partition("=")
        if not separator:
            raise MiraiRuntimeError("seletor deve usar chave=valor")
        normalized_key = normalize_tag_key(key)
        normalized_value = normalize_tag_value(value)
        if normalized_key in parsed:
            raise MiraiRuntimeError(f"seletor repete a chave '{normalized_key}'")
        parsed[normalized_key] = normalized_value
    return parsed


def select_devices(
    devices: dict[str, Device],
    selector: str | None = None,
) -> list[Device]:
    """Seleciona dispositivos por tags e mantém ordem de nome estável."""
    expected = parse_selector(selector)
    selected = [
        device
        for device in devices.values()
        if all(device_tag_map(device).get(key) == value for key, value in expected.items())
    ]
    return sorted(selected, key=lambda item: item.name)


def rollout_batches(
    devices: Sequence[Device],
    *,
    canary_percent: int,
    batch_size: int,
) -> list[list[Device]]:
    """Cria um canário seguido de lotes determinísticos sem duplicação."""
    if not 1 <= canary_percent <= 100:
        raise MiraiRuntimeError("canary_percent deve estar entre 1 e 100")
    if not 1 <= batch_size <= 1_000:
        raise MiraiRuntimeError("batch_size deve estar entre 1 e 1000")
    ordered = sorted(devices, key=lambda item: item.name)
    if not ordered:
        return []
    canary_size = max(1, math.ceil(len(ordered) * canary_percent / 100))
    batches = [ordered[:canary_size]]
    remaining = ordered[canary_size:]
    batches.extend(
        remaining[index : index + batch_size] for index in range(0, len(remaining), batch_size)
    )
    return batches


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rollout_one(
    device: Device,
    artifact_path: Path,
    *,
    input_specs: list[str] | None,
    layout: str,
    provider_profile: str,
    signature_path: Path | None,
    launch: Callable[..., LaunchResult],
) -> tuple[dict[str, Any], LaunchResult | None]:
    started_at = _utc_now()
    try:
        launched = launch(
            artifact_path,
            device.name,
            input_specs,
            layout,
            provider_profile=provider_profile,
            signature_path=signature_path,
        )
        return (
            {
                "device": device.name,
                "status": "passed",
                "started_at": started_at,
                "finished_at": _utc_now(),
                "deployment_id": launched.deployment.get("deployment_id"),
                "previous_active_deployment_id": (launched.previous_active_deployment_id),
                "error": None,
                "rollback": None,
            },
            launched,
        )
    except Exception as error:  # noqa: BLE001 - falha individual não derruba o lote.
        return (
            {
                "device": device.name,
                "status": "failed",
                "started_at": started_at,
                "finished_at": _utc_now(),
                "deployment_id": None,
                "previous_active_deployment_id": None,
                "error": str(error),
                "rollback": None,
            },
            None,
        )


def _write_rollout_report(report: dict[str, Any], directory: Path) -> Path:
    root = directory.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{report['run_id']}.json"
    report["report_path"] = str(path)
    try:
        atomic_write_text(
            path,
            strict_json_dumps(report, pretty=True) + "\n",
            mode=0o600,
        )
    except OSError as error:
        raise MiraiRuntimeError(
            f"não foi possível gravar o relatório do rollout: {error}"
        ) from error
    return path


def execute_rollout(
    artifact_path: Path,
    devices: dict[str, Device],
    *,
    selector: str | None = None,
    canary_percent: int = 10,
    batch_size: int = 10,
    max_failure_rate: float = 0.0,
    workers: int = 4,
    input_specs: list[str] | None = None,
    layout: str = "auto",
    provider_profile: str = "auto",
    signature_path: Path | None = None,
    apply: bool = False,
    report_directory: Path = DEFAULT_ROLLOUT_DIRECTORY,
    launch: Callable[..., LaunchResult] = launch_artifact,
    rollback: Callable[[Device, LaunchResult], dict[str, Any]] = rollback_launch,
) -> dict[str, Any]:
    """Executa rollout canário com gate de falhas e rollback global seguro."""
    if not 0.0 <= max_failure_rate <= 1.0 or not math.isfinite(max_failure_rate):
        raise MiraiRuntimeError("max_failure_rate deve estar entre 0 e 1")
    if not 1 <= workers <= MAX_FLEET_WORKERS:
        raise MiraiRuntimeError(f"workers deve estar entre 1 e {MAX_FLEET_WORKERS}")
    if layout not in {"auto", "nchw", "nhwc"}:
        raise MiraiRuntimeError("layout deve ser auto, nchw ou nhwc")
    provider_profile = normalize_provider_profile(provider_profile)
    selected = select_devices(devices, selector)
    batches = rollout_batches(
        selected,
        canary_percent=canary_percent,
        batch_size=batch_size,
    )
    if not selected:
        raise MiraiRuntimeError("nenhum dispositivo corresponde ao seletor")
    artifact = artifact_path.expanduser().resolve()
    validate_artifact(artifact)
    signature: Path | None = None
    if signature_path is not None:
        signature = signature_path.expanduser().resolve()
        if not signature.is_file() or signature.stat().st_size <= 0:
            raise MiraiRuntimeError(f"assinatura não encontrada ou vazia: {signature}")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": _utc_now(),
        "finished_at": None,
        "status": "planned" if not apply else "running",
        "artifact": str(artifact),
        "signature": str(signature) if signature is not None else None,
        "selector": parse_selector(selector),
        "policy": {
            "canary_percent": canary_percent,
            "batch_size": batch_size,
            "max_failure_rate": max_failure_rate,
            "workers": workers,
            "provider_profile": provider_profile,
        },
        "batches": [[device.name for device in batch] for batch in batches],
        "results": [],
        "rollback": [],
        "skipped": [],
    }
    if not apply:
        report["finished_at"] = _utc_now()
        _write_rollout_report(report, report_directory)
        return report

    succeeded: list[tuple[Device, LaunchResult, dict[str, Any]]] = []
    stopped = False
    for batch_index, batch in enumerate(batches):
        worker_count = min(workers, len(batch))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    _rollout_one,
                    device,
                    artifact,
                    input_specs=input_specs,
                    layout=layout,
                    provider_profile=provider_profile,
                    signature_path=signature,
                    launch=launch,
                ): device
                for device in batch
            }
            batch_results: list[dict[str, Any]] = []
            for future in as_completed(futures):
                item, launched = future.result()
                item["batch"] = batch_index
                batch_results.append(item)
                if launched is not None:
                    succeeded.append((futures[future], launched, item))
            batch_results.sort(key=lambda item: item["device"])
            report["results"].extend(batch_results)

        attempted = len(report["results"])
        failed = sum(item["status"] == "failed" for item in report["results"])
        if attempted and failed / attempted > max_failure_rate:
            stopped = True
            remaining_names = [
                device.name for later_batch in batches[batch_index + 1 :] for device in later_batch
            ]
            report["skipped"].extend(remaining_names)
            break

    if stopped:
        for device, launched, item in reversed(succeeded):
            try:
                rollback_result = rollback(device, launched)
                item["status"] = "rolled_back"
                item["rollback"] = rollback_result
                report["rollback"].append({"device": device.name, **rollback_result})
            except Exception as error:  # noqa: BLE001 - evidência da falha é preservada.
                item["status"] = "rollback_failed"
                item["rollback"] = {"status": "failed", "error": str(error)}
                report["rollback"].append(
                    {"device": device.name, "status": "failed", "error": str(error)}
                )
        report["status"] = (
            "rollback_failed"
            if any(item["status"] == "rollback_failed" for item in report["results"])
            else "rolled_back"
        )
    else:
        report["status"] = "passed"

    report["finished_at"] = _utc_now()
    _write_rollout_report(report, report_directory)
    return report
