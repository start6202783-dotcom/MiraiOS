"""Testes de integridade e portabilidade do Evidence Bundle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mirai.errors import MiraiRuntimeError
from mirai.evidence import (
    create_evidence_bundle,
    load_evidence_bundle,
    verify_evidence_bundle,
)
from mirai.passport import create_model_passport


def test_evidence_bundle_collects_companions_and_passport(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    report = tmp_path / "pilot.json"
    report.write_text('{"schema_version":1,"status":"passed"}\n', encoding="utf-8")
    report.with_suffix(".md").write_text("# Pilot\n", encoding="utf-8")
    passport = tmp_path / "passport.json"
    create_model_passport(dummy_model, passport)
    output = tmp_path / "pilot.mirai-evidence"

    created = create_evidence_bundle(
        report,
        output,
        artifact_path=dummy_model,
        passport_path=passport,
    )
    verified = verify_evidence_bundle(output, dummy_model)

    roles = {item["role"] for item in verified["items"]}
    assert created["bundle_id"] == verified["bundle_id"]
    assert roles == {"report-json", "report-markdown", "model-passport"}
    assert verified["subject"]["included"] is False
    assert verified["claims"]["independent_certification"] is False


def test_evidence_bundle_is_deterministic_for_same_inputs(
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.json"
    report.write_text('{"status":"passed"}\n', encoding="utf-8")
    first = tmp_path / "first.mirai-evidence"
    second = tmp_path / "second.mirai-evidence"

    create_evidence_bundle(report, first)
    create_evidence_bundle(report, second)

    assert first.read_bytes() == second.read_bytes()


def test_evidence_bundle_rejects_tampered_embedded_data(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text('{"status":"passed"}\n', encoding="utf-8")
    output = tmp_path / "report.mirai-evidence"
    create_evidence_bundle(report, output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["items"][0]["data"] = "e30="
    output.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="tamanho|SHA-256"):
        load_evidence_bundle(output)


def test_evidence_bundle_rejects_wrong_artifact(
    dummy_model: Path,
    dummy_package: Path,
    tmp_path: Path,
) -> None:
    report = tmp_path / "report.json"
    report.write_text("{}\n", encoding="utf-8")
    output = tmp_path / "report.mirai-evidence"
    create_evidence_bundle(report, output, artifact_path=dummy_model)

    with pytest.raises(MiraiRuntimeError, match="não corresponde"):
        verify_evidence_bundle(output, dummy_package)
