"""Testes de captura, privacidade e reprodução da v0.14."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mirai.errors import MiraiRuntimeError
from mirai.replay import create_replay_bundle, load_replay_bundle, run_replay


def test_replay_requires_explicit_capture_consent(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    with pytest.raises(MiraiRuntimeError, match="--capture-inputs"):
        create_replay_bundle(dummy_model, tmp_path / "run.mirai-replay")


def test_replay_captures_json_file_without_local_path(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "private-input.json"
    fixture.write_text("5.0\n", encoding="utf-8")
    bundle = tmp_path / "run.mirai-replay"

    create_replay_bundle(
        dummy_model,
        bundle,
        input_specs=[str(fixture)],
        capture_inputs=True,
    )
    replay = load_replay_bundle(bundle)
    result = run_replay(bundle, dummy_model)

    assert result["passed"] is True
    assert result["actual"] == 6.0
    assert replay["privacy"]["capture_was_explicit"] is True
    assert str(tmp_path) not in bundle.read_text(encoding="utf-8")
    assert replay["execution"]["input_specs"][0].startswith("@attachment:")


def test_replay_rejects_tampered_content(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "run.mirai-replay"
    create_replay_bundle(
        dummy_model,
        bundle,
        input_specs=["2.0"],
        capture_inputs=True,
    )
    payload = json.loads(bundle.read_text(encoding="utf-8"))
    payload["expected"]["result"] = 999
    bundle.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="identificador"):
        load_replay_bundle(bundle)


def test_replay_rejects_different_artifact(
    dummy_model: Path,
    model_factory: object,
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "run.mirai-replay"
    create_replay_bundle(dummy_model, bundle, capture_inputs=True)
    factory = model_factory
    other_model = factory("other", [1])  # type: ignore[operator]

    with pytest.raises(MiraiRuntimeError, match="não corresponde"):
        run_replay(bundle, other_model)
