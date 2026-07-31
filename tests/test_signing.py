"""Testes de assinaturas destacadas DSSE/Ed25519."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from mirai.errors import MiraiRuntimeError
from mirai.signing import (
    DSSE_PAYLOAD_TYPE,
    generate_signing_key,
    sign_artifact,
    signing_key_paths,
    verify_artifact,
)


def _keys(tmp_path: Path, name: str = "release") -> tuple[Path, Path]:
    private = tmp_path / f"{name}.key"
    public = tmp_path / f"{name}.pub"
    generate_signing_key(private, public)
    return private, public


def test_generate_signing_key_creates_private_and_public_files(tmp_path: Path) -> None:
    private, public = _keys(tmp_path)

    assert "PRIVATE KEY" in private.read_text(encoding="ascii")
    assert "PUBLIC KEY" in public.read_text(encoding="ascii")
    if os.name != "nt":
        assert stat.S_IMODE(private.stat().st_mode) == 0o600
        assert stat.S_IMODE(public.stat().st_mode) == 0o644


def test_generate_signing_key_refuses_overwrite(tmp_path: Path) -> None:
    private, public = _keys(tmp_path)
    with pytest.raises(MiraiRuntimeError, match="já existe"):
        generate_signing_key(private, public)


def test_generate_signing_key_can_replace_explicitly(tmp_path: Path) -> None:
    private, public = _keys(tmp_path)
    before = public.read_bytes()

    generate_signing_key(private, public, replace=True)

    assert public.read_bytes() != before


def test_generate_signing_key_rejects_same_target(tmp_path: Path) -> None:
    target = tmp_path / "same.pem"
    with pytest.raises(MiraiRuntimeError, match="distintos"):
        generate_signing_key(target, target)


@pytest.mark.parametrize("name", ["", "../escape", "with space", ".hidden", "a" * 65])
def test_signing_key_paths_reject_unsafe_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(MiraiRuntimeError, match="nome de chave inválido"):
        signing_key_paths(tmp_path, name)


def test_sign_and_verify_pilot_report(tmp_path: Path) -> None:
    private, public = _keys(tmp_path)
    report = tmp_path / "pilot.json"
    report.write_text('{"status":"passed"}\n', encoding="utf-8")

    signed = sign_artifact(report, private)
    verified = verify_artifact(report, signed["signature"], public)

    assert verified["valid"] is True
    assert verified["payload"]["kind"] == "pilot-report"
    assert verified["payload"]["name"] == report.name
    assert len(verified["payload"]["sha256"]) == 64


def test_sign_and_verify_mirai_package(dummy_package: Path, tmp_path: Path) -> None:
    private, public = _keys(tmp_path)
    signed = sign_artifact(dummy_package, private)

    verified = verify_artifact(dummy_package, signed["signature"], public)

    assert verified["payload"]["kind"] == "mirai-package"


def test_verify_rejects_modified_artifact(tmp_path: Path) -> None:
    private, public = _keys(tmp_path)
    report = tmp_path / "pilot.json"
    report.write_text("{}\n", encoding="utf-8")
    signed = sign_artifact(report, private)
    report.write_text('{"tampered":true}\n', encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="não corresponde"):
        verify_artifact(report, signed["signature"], public)


def test_verify_rejects_wrong_public_key(tmp_path: Path) -> None:
    private, _ = _keys(tmp_path, "first")
    _, wrong_public = _keys(tmp_path, "second")
    report = tmp_path / "pilot.json"
    report.write_text("{}\n", encoding="utf-8")
    signed = sign_artifact(report, private)

    with pytest.raises(MiraiRuntimeError, match="não pertence"):
        verify_artifact(report, signed["signature"], wrong_public)


def test_verify_rejects_modified_signature(tmp_path: Path) -> None:
    private, public = _keys(tmp_path)
    report = tmp_path / "pilot.json"
    report.write_text("{}\n", encoding="utf-8")
    signed = sign_artifact(report, private)
    envelope = json.loads(Path(signed["signature"]).read_text(encoding="utf-8"))
    envelope["signatures"][0]["sig"] = "A" * 88
    Path(signed["signature"]).write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="assinatura Ed25519 inválida"):
        verify_artifact(report, signed["signature"], public)


def test_verify_rejects_unknown_envelope_field(tmp_path: Path) -> None:
    private, public = _keys(tmp_path)
    report = tmp_path / "pilot.json"
    report.write_text("{}\n", encoding="utf-8")
    signed = sign_artifact(report, private)
    envelope = json.loads(Path(signed["signature"]).read_text(encoding="utf-8"))
    envelope["unsafe"] = True
    Path(signed["signature"]).write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="campos incompatíveis"):
        verify_artifact(report, signed["signature"], public)


def test_verify_rejects_wrong_payload_type(tmp_path: Path) -> None:
    private, public = _keys(tmp_path)
    report = tmp_path / "pilot.json"
    report.write_text("{}\n", encoding="utf-8")
    signed = sign_artifact(report, private)
    envelope = json.loads(Path(signed["signature"]).read_text(encoding="utf-8"))
    assert envelope["payloadType"] == DSSE_PAYLOAD_TYPE
    envelope["payloadType"] = "text/plain"
    Path(signed["signature"]).write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="tipo de payload"):
        verify_artifact(report, signed["signature"], public)


def test_sign_rejects_unsupported_artifact_type(tmp_path: Path) -> None:
    private, _ = _keys(tmp_path)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"model")

    with pytest.raises(MiraiRuntimeError, match="somente pacotes"):
        sign_artifact(model, private)


def test_sign_never_replaces_artifact_with_envelope(tmp_path: Path) -> None:
    private, _ = _keys(tmp_path)
    report = tmp_path / "pilot.json"
    original = b"{}\n"
    report.write_bytes(original)

    with pytest.raises(MiraiRuntimeError, match="próprio artefato"):
        sign_artifact(report, private, report, replace=True)

    assert report.read_bytes() == original


@pytest.mark.skipif(os.name == "nt", reason="permissões POSIX")
def test_sign_rejects_world_readable_private_key(tmp_path: Path) -> None:
    private, _ = _keys(tmp_path)
    private.chmod(0o644)
    report = tmp_path / "pilot.json"
    report.write_text("{}\n", encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="chmod 600"):
        sign_artifact(report, private)
