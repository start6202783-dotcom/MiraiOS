"""Testes da Compatibility Matrix local e não certificadora."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mirai.arena import create_arena_result
from mirai.compatibility import (
    add_compatibility_result,
    load_compatibility_matrix,
)
from mirai.errors import MiraiRuntimeError
from mirai.signing import generate_signing_key


def test_matrix_adds_result_idempotently_and_never_self_certifies(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    result = tmp_path / "arena.json"
    matrix_path = tmp_path / "matrix.json"
    create_arena_result(dummy_model, result, runs=3, warmup_runs=0)

    first = add_compatibility_result(matrix_path, result)
    second = add_compatibility_result(matrix_path, result)

    assert first == second
    assert first["summary"]["result_count"] == 1
    assert first["summary"]["hardware_count"] == 1
    readiness = first["summary"]["readiness"][0]
    assert readiness["status"] == "observed"
    assert readiness["certification_status"] == "not-certified"
    assert first["summary"]["certification"]["self_certification_supported"] is False


def test_matrix_verifies_signed_arena_evidence(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    private = tmp_path / "arena.key"
    public = tmp_path / "arena.pub"
    generate_signing_key(private, public)
    result = tmp_path / "arena.json"
    outcome = create_arena_result(
        dummy_model,
        result,
        runs=3,
        warmup_runs=0,
        signing_key_path=private,
    )

    matrix_path = tmp_path / "matrix.json"
    initial = add_compatibility_result(matrix_path, result)
    matrix = add_compatibility_result(
        matrix_path,
        result,
        signature_path=outcome.signature_path,
        public_key_path=public,
    )

    assert initial["entries"][0]["verification"]["level"] == "self-measured"
    assert matrix["entries"][0]["verification"]["level"] == "signed-verified"
    assert matrix["summary"]["readiness"][0]["status"] == "signed-evidence"


def test_matrix_detects_tampered_summary(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    result = tmp_path / "arena.json"
    matrix_path = tmp_path / "matrix.json"
    create_arena_result(dummy_model, result, runs=2, warmup_runs=0)
    add_compatibility_result(matrix_path, result)
    payload = json.loads(matrix_path.read_text(encoding="utf-8"))
    payload["summary"]["hardware_count"] = 99
    matrix_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="resumo"):
        load_compatibility_matrix(matrix_path)


def test_matrix_requires_signature_and_key_together(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    result = tmp_path / "arena.json"
    create_arena_result(dummy_model, result, runs=2, warmup_runs=0)

    with pytest.raises(MiraiRuntimeError, match="juntos"):
        add_compatibility_result(
            tmp_path / "matrix.json",
            result,
            signature_path=tmp_path / "missing.sig",
        )
