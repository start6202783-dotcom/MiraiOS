"""Testes da identidade e das credenciais do Hikari Link."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mirai.security import (
    AgentSecurity,
    AuthenticationDenied,
    PairingDenied,
    normalize_fingerprint,
)


def test_secure_identity_is_persistent_and_private(tmp_path: Path) -> None:
    first = AgentSecurity(tmp_path, secure=True)
    second = AgentSecurity(tmp_path, secure=True)

    assert first.agent_id == second.agent_id
    assert first.fingerprint == second.fingerprint
    assert first.pairing_code != second.pairing_code
    assert len(normalize_fingerprint(first.fingerprint or "")) == 64

    if os.name != "nt":
        key_mode = stat.S_IMODE((tmp_path / "agent-key.pem").stat().st_mode)
        identity_mode = stat.S_IMODE(
            (tmp_path / "identity.json").stat().st_mode
        )
        assert key_mode == 0o600
        assert identity_mode == 0o600


def test_pairing_code_is_single_use_and_token_is_hashed(
    tmp_path: Path,
) -> None:
    security = AgentSecurity(tmp_path, secure=True)
    pairing_code = security.pairing_code
    assert pairing_code is not None

    pairing = security.pair("notebook", pairing_code)
    clients_file = (tmp_path / "clients.json").read_text(encoding="utf-8")
    stored = json.loads(clients_file)["clients"][0]

    assert pairing["token"] not in clients_file
    assert stored["token_sha256"] == hashlib.sha256(
        pairing["token"].encode("ascii")
    ).hexdigest()
    assert security.authenticate(pairing["token"])["name"] == "notebook"

    with pytest.raises(PairingDenied, match="expirado ou já utilizado"):
        security.pair("outro", pairing_code)

    revoked = security.revoke(pairing["token"])
    assert revoked["client_id"] == pairing["client_id"]
    with pytest.raises(AuthenticationDenied, match="revogado"):
        security.authenticate(pairing["token"])


def test_pairing_code_expires(tmp_path: Path) -> None:
    current_time = datetime(2026, 7, 28, 12, tzinfo=timezone.utc)

    def clock() -> datetime:
        return current_time

    security = AgentSecurity(tmp_path, secure=True, clock=clock)
    pairing_code = security.pairing_code
    assert pairing_code is not None

    current_time += timedelta(minutes=11)

    with pytest.raises(PairingDenied, match="expirado"):
        security.pair("notebook", pairing_code)


def test_authentication_rejects_malformed_token(tmp_path: Path) -> None:
    security = AgentSecurity(tmp_path, secure=True)

    with pytest.raises(AuthenticationDenied, match="ausente ou inválido"):
        security.authenticate(None)
    with pytest.raises(AuthenticationDenied, match="ausente ou inválido"):
        security.authenticate("curto")
