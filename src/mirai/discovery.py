"""Descoberta mDNS opcional; candidatos encontrados continuam não confiáveis."""

from __future__ import annotations

import math
import socket
import time
from dataclasses import dataclass
from typing import Any

from .errors import MiraiRuntimeError

SERVICE_TYPE = "_miraios._tcp.local."


def _zeroconf_types() -> tuple[Any, Any, Any, Any]:
    try:
        from zeroconf import ServiceBrowser, ServiceInfo, ServiceStateChange, Zeroconf
    except ModuleNotFoundError as error:
        raise MiraiRuntimeError(
            "descoberta mDNS exige o extra opcional: pip install 'miraios[discovery]'"
        ) from error
    return Zeroconf, ServiceBrowser, ServiceInfo, ServiceStateChange


@dataclass(frozen=True, slots=True)
class DiscoveredAgent:
    name: str
    url: str
    agent_id: str | None
    tls: bool
    trusted: bool = False


class AgentAdvertiser:
    """Publica somente metadados não secretos do Agent na rede local."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        agent_id: str | None,
        tls: bool,
        version: str,
    ) -> None:
        Zeroconf, _, ServiceInfo, _ = _zeroconf_types()
        try:
            address = socket.inet_aton(socket.gethostbyname(host))
        except OSError as error:
            raise MiraiRuntimeError(f"host mDNS inválido: {host}") from error
        safe_id = (agent_id or "local")[:32]
        self._zeroconf = Zeroconf()
        self._info = ServiceInfo(
            SERVICE_TYPE,
            f"MiraiOS-{safe_id}.{SERVICE_TYPE}",
            addresses=[address],
            port=port,
            properties={
                b"agent_id": (agent_id or "").encode("ascii"),
                b"tls": b"1" if tls else b"0",
                b"version": version.encode("ascii"),
            },
            server=f"{socket.gethostname()}.local.",
        )
        self._zeroconf.register_service(self._info)

    def close(self) -> None:
        self._zeroconf.unregister_service(self._info)
        self._zeroconf.close()


def discover_agents(timeout: float = 2.0) -> list[DiscoveredAgent]:
    """Coleta anúncios link-local, explicitamente marcados como não confiáveis."""
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 30:
        raise MiraiRuntimeError("timeout de descoberta deve estar entre 0 e 30 segundos")
    Zeroconf, ServiceBrowser, _, ServiceStateChange = _zeroconf_types()
    zeroconf = Zeroconf()
    found: dict[str, DiscoveredAgent] = {}

    def on_change(
        current: Any,
        service_type: str,
        name: str,
        state_change: Any,
    ) -> None:
        if state_change not in {ServiceStateChange.Added, ServiceStateChange.Updated}:
            return
        info = current.get_service_info(service_type, name, timeout=1000)
        if info is None:
            return
        addresses = info.parsed_addresses()
        if not addresses:
            return
        properties = {
            key.decode("utf-8", "replace"): value.decode("utf-8", "replace")
            for key, value in info.properties.items()
        }
        tls = properties.get("tls") == "1"
        scheme = "https" if tls else "http"
        found[name] = DiscoveredAgent(
            name=name.removesuffix(f".{SERVICE_TYPE}"),
            url=f"{scheme}://{addresses[0]}:{info.port}",
            agent_id=properties.get("agent_id") or None,
            tls=tls,
        )

    browser = ServiceBrowser(zeroconf, SERVICE_TYPE, handlers=[on_change])
    try:
        time.sleep(timeout)
    finally:
        browser.cancel()
        zeroconf.close()
    return sorted(found.values(), key=lambda item: item.name)
