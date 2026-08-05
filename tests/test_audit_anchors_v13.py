"""Testes de prova de extensão e ancoragem externa da auditoria v0.13."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

import mirai.anchors as anchors_module
from mirai.anchors import anchor_device, anchor_fleet
from mirai.audit import AUDIT_GENESIS_HASH, AuditLog
from mirai.devices import Device
from mirai.errors import MiraiRuntimeError

AGENT_A = "a" * 32
AGENT_B = "b" * 32


def _device(name: str = "edge", *, agent_id: str | None = AGENT_A) -> Device:
    return Device(
        name,
        "http://127.0.0.1:8080",
        agent_id=agent_id,
    )


def test_empty_audit_extends_genesis(tmp_path: Path) -> None:
    proof = AuditLog(tmp_path / "audit.jsonl").extension(0, AUDIT_GENESIS_HASH)

    assert proof["valid"] is True
    assert proof["records"] == 0
    assert proof["proof"] == []


def test_audit_extension_proves_every_new_link(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    first = audit.append({"type": "first"})
    second = audit.append({"type": "second"})
    third = audit.append({"type": "third"})

    proof = audit.extension(1, first["record_hash"])

    assert proof["records"] == 3
    assert [item["sequence"] for item in proof["proof"]] == [2, 3]
    assert proof["proof"][0]["previous_hash"] == first["record_hash"]
    assert proof["proof"][0]["record_hash"] == second["record_hash"]
    assert proof["proof"][1]["record_hash"] == third["record_hash"]
    assert proof["head"] == third["record_hash"]


@pytest.mark.parametrize(
    ("records", "head"),
    [
        (-1, AUDIT_GENESIS_HASH),
        (True, AUDIT_GENESIS_HASH),
        (0, "invalid"),
        (1, AUDIT_GENESIS_HASH),
    ],
)
def test_audit_extension_rejects_invalid_or_divergent_checkpoint(
    tmp_path: Path,
    records: Any,
    head: str,
) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")

    with pytest.raises(MiraiRuntimeError):
        audit.extension(records, head)


def test_audit_extension_caps_response_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    monkeypatch.setattr(
        audit,
        "verify",
        lambda: {"valid": True, "version": 1, "records": 10_001, "head": "f" * 64},
    )

    with pytest.raises(MiraiRuntimeError, match="10000"):
        audit.extension(0, AUDIT_GENESIS_HASH)


def test_anchor_device_creates_then_reuses_external_checkpoint(tmp_path: Path) -> None:
    remote = AuditLog(tmp_path / "remote" / "audit.jsonl")
    remote.append({"type": "deployment"})
    ledger_path = tmp_path / "control" / "anchors.jsonl"
    device = _device()

    def health(_: Device) -> dict[str, Any]:
        return {"agent_id": AGENT_A}

    def audit(_: Device, **checkpoint: Any) -> dict[str, Any]:
        if checkpoint:
            return remote.extension(checkpoint["from_records"], checkpoint["from_head"])
        return remote.verify()

    first = anchor_device(device, ledger_path=ledger_path, health=health, audit=audit)
    unchanged = anchor_device(device, ledger_path=ledger_path, health=health, audit=audit)
    remote.append({"type": "activation"})
    extended = anchor_device(device, ledger_path=ledger_path, health=health, audit=audit)

    assert first["status"] == "anchored"
    assert first["ledger_sequence"] == 1
    assert unchanged["status"] == "unchanged"
    assert extended["status"] == "anchored"
    assert extended["records"] == 2
    assert extended["ledger_sequence"] == 2
    assert AuditLog(ledger_path).verify()["records"] == 2


def test_anchor_rejects_agent_identity_change(tmp_path: Path) -> None:
    with pytest.raises(MiraiRuntimeError, match="diverge"):
        anchor_device(
            _device(),
            ledger_path=tmp_path / "anchors.jsonl",
            health=lambda _: {"agent_id": AGENT_B},
            audit=lambda _: {
                "valid": True,
                "version": 1,
                "records": 0,
                "head": AUDIT_GENESIS_HASH,
            },
        )


@pytest.mark.parametrize(
    "status",
    [
        {},
        {"valid": False, "version": 1, "records": 0, "head": AUDIT_GENESIS_HASH},
        {"valid": True, "version": 1, "records": True, "head": AUDIT_GENESIS_HASH},
        {"valid": True, "version": 1, "records": 0, "head": "f" * 64},
    ],
)
def test_anchor_rejects_invalid_remote_checkpoint(
    tmp_path: Path,
    status: dict[str, Any],
) -> None:
    with pytest.raises(MiraiRuntimeError):
        anchor_device(
            _device(agent_id=None),
            ledger_path=tmp_path / "anchors.jsonl",
            health=lambda _: {"agent_id": AGENT_A},
            audit=lambda _: status,
        )


def test_anchor_rejects_incomplete_extension_proof(tmp_path: Path) -> None:
    ledger_path = tmp_path / "anchors.jsonl"
    device = _device()
    initial = {"valid": True, "version": 1, "records": 1, "head": "1" * 64}
    anchor_device(
        device,
        ledger_path=ledger_path,
        health=lambda _: {"agent_id": AGENT_A},
        audit=lambda _: initial,
    )

    def rewritten(_: Device, **checkpoint: Any) -> dict[str, Any]:
        return {
            "valid": True,
            "version": 1,
            "records": 2,
            "head": "2" * 64,
            "from_records": checkpoint["from_records"],
            "from_head": checkpoint["from_head"],
            "proof": [],
        }

    with pytest.raises(MiraiRuntimeError, match="incompleta"):
        anchor_device(
            device,
            ledger_path=ledger_path,
            health=lambda _: {"agent_id": AGENT_A},
            audit=rewritten,
        )


@pytest.mark.parametrize(
    "proof",
    [
        [{"sequence": 2, "previous_hash": "1" * 64, "record_hash": "bad"}],
        [{"sequence": 99, "previous_hash": "1" * 64, "record_hash": "2" * 64}],
    ],
)
def test_anchor_rejects_malformed_extension_link(
    tmp_path: Path,
    proof: list[dict[str, Any]],
) -> None:
    ledger_path = tmp_path / "anchors.jsonl"
    device = _device()
    anchor_device(
        device,
        ledger_path=ledger_path,
        health=lambda _: {"agent_id": AGENT_A},
        audit=lambda _: {"valid": True, "version": 1, "records": 1, "head": "1" * 64},
    )

    with pytest.raises(MiraiRuntimeError, match="não confere"):
        anchor_device(
            device,
            ledger_path=ledger_path,
            health=lambda _: {"agent_id": AGENT_A},
            audit=lambda *_args, **checkpoint: {
                "valid": True,
                "version": 1,
                "records": 2,
                "head": "2" * 64,
                "from_records": checkpoint["from_records"],
                "from_head": checkpoint["from_head"],
                "proof": proof,
            },
        )


def test_anchor_rejects_proof_that_does_not_reach_current_head(tmp_path: Path) -> None:
    ledger_path = tmp_path / "anchors.jsonl"
    device = _device()
    anchor_device(
        device,
        ledger_path=ledger_path,
        health=lambda _: {"agent_id": AGENT_A},
        audit=lambda _: {"valid": True, "version": 1, "records": 1, "head": "1" * 64},
    )

    with pytest.raises(MiraiRuntimeError, match="não alcança"):
        anchor_device(
            device,
            ledger_path=ledger_path,
            health=lambda _: {"agent_id": AGENT_A},
            audit=lambda *_args, **checkpoint: {
                "valid": True,
                "version": 1,
                "records": 2,
                "head": "3" * 64,
                "from_records": checkpoint["from_records"],
                "from_head": checkpoint["from_head"],
                "proof": [
                    {
                        "sequence": 2,
                        "previous_hash": "1" * 64,
                        "record_hash": "2" * 64,
                    }
                ],
            },
        )


@pytest.mark.parametrize("agent_id", [None, "invalid", "A" * 32])
def test_anchor_rejects_invalid_health_identity(tmp_path: Path, agent_id: Any) -> None:
    with pytest.raises(MiraiRuntimeError, match="identidade inválida"):
        anchor_device(
            _device(agent_id=None),
            ledger_path=tmp_path / "anchors.jsonl",
            health=lambda _: {"agent_id": agent_id},
            audit=lambda _: {},
        )


def test_anchor_rejects_semantically_invalid_anchor_event(tmp_path: Path) -> None:
    ledger_path = tmp_path / "anchors.jsonl"
    AuditLog(ledger_path).append(
        {
            "type": "audit_anchor",
            "agent_id": AGENT_A,
            "records": "one",
            "head": "1" * 64,
        }
    )

    with pytest.raises(MiraiRuntimeError, match="âncora inválida"):
        anchor_device(
            _device(),
            ledger_path=ledger_path,
            health=lambda _: {"agent_id": AGENT_A},
            audit=lambda *_args, **_kwargs: {},
        )


def test_anchor_stops_after_repeated_concurrent_checkpoint_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def changing(*_: Any) -> dict[str, Any] | None:
        nonlocal calls
        calls += 1
        if calls % 2:
            return None
        return {"records": calls, "head": "f" * 64}

    monkeypatch.setattr(anchors_module, "_latest_anchor", changing)

    with pytest.raises(MiraiRuntimeError, match="concorrentemente"):
        anchor_device(
            _device(agent_id=None),
            ledger_path=tmp_path / "anchors.jsonl",
            health=lambda _: {"agent_id": AGENT_A},
            audit=lambda _: {
                "valid": True,
                "version": 1,
                "records": 0,
                "head": AUDIT_GENESIS_HASH,
            },
        )


def test_anchor_fleet_preserves_partial_failure_and_order(tmp_path: Path) -> None:
    devices = [_device("zeta"), _device("alpha")]

    def anchor(device: Device, **_: Any) -> dict[str, Any]:
        if device.name == "zeta":
            raise MiraiRuntimeError("unreachable")
        return {"device": device.name, "status": "anchored"}

    results = anchor_fleet(devices, ledger_path=tmp_path / "anchors.jsonl", anchor=anchor)

    assert [item["device"] for item in results] == ["alpha", "zeta"]
    assert results[0]["status"] == "anchored"
    assert results[1]["status"] == "failed"
    assert "unreachable" in results[1]["error"]


@pytest.mark.parametrize("workers", [0, 33])
def test_anchor_fleet_rejects_invalid_worker_count(workers: int) -> None:
    with pytest.raises(MiraiRuntimeError, match="workers"):
        anchor_fleet([_device()], workers=workers)


def test_anchor_fleet_empty_input_is_empty() -> None:
    assert anchor_fleet([]) == []


def test_anchor_fleet_allows_remote_checks_to_run_concurrently(tmp_path: Path) -> None:
    barrier = threading.Barrier(2, timeout=5)
    devices = [_device("a", agent_id=None), _device("b", agent_id=None)]

    def health(device: Device) -> dict[str, Any]:
        return {"agent_id": AGENT_A if device.name == "a" else AGENT_B}

    def audit(_: Device, **__: Any) -> dict[str, Any]:
        barrier.wait()
        return {
            "valid": True,
            "version": 1,
            "records": 0,
            "head": AUDIT_GENESIS_HASH,
        }

    def anchor(device: Device, *, ledger_path: Path) -> dict[str, Any]:
        return anchor_device(device, ledger_path=ledger_path, health=health, audit=audit)

    results = anchor_fleet(
        devices,
        ledger_path=tmp_path / "anchors.jsonl",
        workers=2,
        anchor=anchor,
    )

    assert [item["status"] for item in results] == ["anchored", "anchored"]
    assert AuditLog(tmp_path / "anchors.jsonl").verify()["records"] == 2
