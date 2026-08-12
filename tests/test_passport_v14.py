"""Testes do Model Passport da v0.14."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mirai.errors import MiraiRuntimeError
from mirai.passport import (
    create_model_passport,
    load_model_passport,
    verify_model_passport,
)


def test_model_passport_is_deterministic_and_verifiable(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    options = {
        "author": "Projeto Hikari",
        "license_expression": "MIT",
        "intended_use": "Demonstrações locais",
        "limitations": ["Não validado para decisões críticas"],
    }

    create_model_passport(dummy_model, first, **options)
    create_model_passport(dummy_model, second, **options)
    passport = verify_model_passport(first, dummy_model)

    assert first.read_bytes() == second.read_bytes()
    assert passport["format"] == "mirai.model-passport"
    assert passport["model"]["format"] == "onnx"
    assert passport["model"]["precision"] == "fp32"
    assert passport["contract"]["inputs"][0]["name"] == "input"
    assert passport["provenance"]["claims_are_user_supplied"] is True


def test_package_passport_preserves_package_identity(
    dummy_package: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "package-passport.json"

    passport = create_model_passport(dummy_package, output)

    assert passport["subject"]["kind"] == "mirai-package"
    assert passport["package"]["name"] == "dummy"
    assert passport["package"]["format_version"] == 1


def test_passport_rejects_tampered_subject(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "passport.json"
    create_model_passport(dummy_model, output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["subject"]["sha256"] = "0" * 64
    output.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="não corresponde"):
        verify_model_passport(output, dummy_model)


def test_passport_rejects_unknown_top_level_field(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "passport.json"
    create_model_passport(dummy_model, output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["unknown"] = True
    output.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="campos incompatíveis"):
        load_model_passport(output)
