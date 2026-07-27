"""Testes de validação e inspeção ONNX."""

from __future__ import annotations

from pathlib import Path

import pytest

from mirai.errors import MiraiRuntimeError
from mirai.inspect import show_model_info, validate_model


def test_validate_accepts_well_formed_model(dummy_model: Path) -> None:
    assert validate_model(dummy_model) > 0


def test_validate_rejects_corrupt_onnx(tmp_path: Path) -> None:
    fake_model = tmp_path / "fake.onnx"
    fake_model.write_text("não é um modelo", encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="falha ao carregar"):
        validate_model(fake_model)


def test_validate_rejects_wrong_extension(tmp_path: Path) -> None:
    wrong_extension = tmp_path / "model.txt"
    wrong_extension.write_text("conteúdo", encoding="utf-8")

    with pytest.raises(MiraiRuntimeError, match="extensão .onnx"):
        validate_model(wrong_extension)


def test_show_model_info_lists_structure(
    dummy_model: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    show_model_info(dummy_model)
    output = capsys.readouterr().out

    assert "Entradas" in output
    assert "Saídas" in output
    assert "Shape: [1]" in output
    assert "Tipo: FLOAT" in output
    assert "Total de nós: 1" in output
