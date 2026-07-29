"""Testes da interface de linha de comando."""

from __future__ import annotations

from pathlib import Path

import pytest

from mirai import __version__
from mirai.cli import main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert f"v{__version__}" in capsys.readouterr().out


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "comandos disponíveis" in capsys.readouterr().out


def test_validate_command(dummy_model: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(dummy_model)]) == 0
    assert "Modelo ONNX válido" in capsys.readouterr().out


def test_pack_validate_info_and_run_commands(
    dummy_model: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    package_path = tmp_path / "example.mirai"

    assert (
        main(
            [
                "pack",
                str(dummy_model),
                "--name",
                "example",
                "--package-version",
                "1.0.0",
                "--description",
                "Exemplo da CLI",
                "--output",
                str(package_path),
            ]
        )
        == 0
    )
    assert main(["validate", str(package_path)]) == 0
    assert main(["info", str(package_path)]) == 0
    assert main(["run", str(package_path), "--input", "5.0"]) == 0

    output = capsys.readouterr().out
    assert "Pacote criado" in output
    assert "Pacote .mirai válido" in output
    assert "Pacote: example v1.0.0" in output
    assert "Resultado da inferência: 6.0" in output


def test_validate_command_rejects_fake_onnx(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake_model = tmp_path / "fake.onnx"
    fake_model.write_text("não é ONNX", encoding="utf-8")

    assert main(["validate", str(fake_model)]) == 1
    assert "falha ao carregar" in capsys.readouterr().err


def test_run_command(dummy_model: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", str(dummy_model), "--input", "5.0"]) == 0
    output = capsys.readouterr().out

    assert "Resultado da inferência: 6.0" in output
    assert "Tempo de inferência" in output


def test_run_command_returns_failure_for_invalid_input(
    dummy_model: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["run", str(dummy_model), "--input", "inválido"]) == 1
    assert "entrada numérica ou JSON inválida" in capsys.readouterr().err


def test_benchmark_reports_stable_statistics(
    dummy_model: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "benchmark",
                str(dummy_model),
                "--runs",
                "5",
                "--warmup",
                "1",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out

    assert "Aquecimento: 1 execuções" in output
    assert "Inferências medidas: 5" in output
    assert "Mediana" in output
    assert "P95" in output
    assert "Inferências por segundo (IPS)" in output
