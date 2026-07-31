"""Visão concorrente e tolerante a falhas de uma pequena frota edge."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .agent_client import doctor_device
from .devices import Device


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
