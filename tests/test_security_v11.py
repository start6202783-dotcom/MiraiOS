"""Testes de RBAC, limitação de pareamento e rotação de identidade."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mirai.errors import MiraiRuntimeError
from mirai.security import (
    AgentSecurity,
    PairingDenied,
    PairingRateLimited,
    role_allows,
    rotate_agent_identity,
)


def test_pairing_grants_configured_role(tmp_path: Path) -> None:
    security = AgentSecurity(tmp_path, secure=True, pairing_role="viewer")
    code = security.pairing_code
    assert code is not None

    pairing = security.pair("dashboard", code)

    assert pairing["role"] == "viewer"
    assert security.authenticate(pairing["token"])["role"] == "viewer"


@pytest.mark.parametrize(
    "actual,required,allowed",
    [
        ("viewer", "viewer", True),
        ("viewer", "operator", False),
        ("operator", "viewer", True),
        ("operator", "admin", False),
        ("admin", "operator", True),
        ("admin", "admin", True),
    ],
)
def test_role_hierarchy(actual: str, required: str, allowed: bool) -> None:
    assert role_allows(actual, required) is allowed


def test_pairing_blocks_after_repeated_failures(tmp_path: Path) -> None:
    security = AgentSecurity(tmp_path, secure=True)
    for _ in range(5):
        with pytest.raises(PairingDenied):
            security.pair("attacker", "AAAA-AAAA-AAAA", peer_id="10.0.0.2")

    with pytest.raises(PairingRateLimited) as blocked:
        security.pair("attacker", "AAAA-AAAA-AAAA", peer_id="10.0.0.2")

    assert blocked.value.retry_after > 0


def test_pairing_limit_is_scoped_by_peer(tmp_path: Path) -> None:
    security = AgentSecurity(tmp_path, secure=True)
    code = security.pairing_code
    assert code is not None
    for _ in range(5):
        with pytest.raises(PairingDenied):
            security.pair("attacker", "AAAA-AAAA-AAAA", peer_id="10.0.0.2")

    pairing = security.pair("owner", code, peer_id="10.0.0.3")

    assert pairing["role"] == "admin"


def test_pairing_limit_expires_after_cooldown(tmp_path: Path) -> None:
    current = datetime(2026, 7, 31, tzinfo=timezone.utc)

    def clock() -> datetime:
        return current

    security = AgentSecurity(tmp_path, secure=True, clock=clock)
    code = security.pairing_code
    assert code is not None
    for _ in range(5):
        with pytest.raises(PairingDenied):
            security.pair("attacker", "AAAA-AAAA-AAAA", peer_id="peer")
    current += timedelta(minutes=6)

    pairing = security.pair("owner", code, peer_id="peer")

    assert pairing["name"] == "owner"


def test_v1_clients_migrate_to_admin_role(tmp_path: Path) -> None:
    security = AgentSecurity(tmp_path, secure=True)
    code = security.pairing_code
    assert code is not None
    pairing = security.pair("legacy", code)
    payload = json.loads((tmp_path / "clients.json").read_text(encoding="utf-8"))
    payload["version"] = 1
    payload["clients"][0].pop("role")
    (tmp_path / "clients.json").write_text(json.dumps(payload), encoding="utf-8")

    client = security.authenticate(pairing["token"])

    assert client["role"] == "admin"
    assert json.loads((tmp_path / "clients.json").read_text(encoding="utf-8"))["version"] == 2


def test_last_admin_cannot_be_demoted(tmp_path: Path) -> None:
    security = AgentSecurity(tmp_path, secure=True)
    code = security.pairing_code
    assert code is not None
    client = security.pair("owner", code)

    with pytest.raises(MiraiRuntimeError, match="último administrador"):
        security.set_client_role(client["client_id"], "operator")


def test_admin_can_be_demoted_when_another_admin_exists(tmp_path: Path) -> None:
    security = AgentSecurity(tmp_path, secure=True)
    first_code = security.pairing_code
    assert first_code is not None
    first = security.pair("first", first_code)
    second_code = security.rotate_pairing_code()
    security.pair("second", second_code)

    changed = security.set_client_role(first["client_id"], "viewer")

    assert changed["role"] == "viewer"


def test_set_role_rejects_unknown_client(tmp_path: Path) -> None:
    security = AgentSecurity(tmp_path, secure=True)
    with pytest.raises(MiraiRuntimeError, match="não encontrado"):
        security.set_client_role("a" * 16, "viewer")


def test_rotate_identity_changes_certificate_and_invalidates_clients(tmp_path: Path) -> None:
    security = AgentSecurity(tmp_path, secure=True)
    code = security.pairing_code
    assert code is not None
    security.pair("owner", code)
    old_agent_id = security.agent_id
    old_fingerprint = security.fingerprint
    assert old_agent_id is not None

    rotated = rotate_agent_identity(tmp_path, old_agent_id)

    assert rotated.agent_id != old_agent_id
    assert rotated.fingerprint != old_fingerprint
    assert not (tmp_path / "clients.json").exists()
    audit_files = list((tmp_path / "identity-history").glob("*.json"))
    assert len(audit_files) == 1
    assert old_agent_id in audit_files[0].name


def test_rotate_identity_requires_exact_confirmation(tmp_path: Path) -> None:
    security = AgentSecurity(tmp_path, secure=True)
    before = security.agent_id

    with pytest.raises(MiraiRuntimeError, match="confirmação incorreta"):
        rotate_agent_identity(tmp_path, "0" * 32)

    assert AgentSecurity(tmp_path, secure=True).agent_id == before
