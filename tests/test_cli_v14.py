"""Fluxos de CLI adicionados na v0.14."""

from __future__ import annotations

from pathlib import Path

import pytest

from mirai.cli import main
from mirai.signing import generate_signing_key


def test_cli_passport_replay_arena_matrix_and_evidence_flow(
    dummy_model: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    passport = tmp_path / "passport.json"
    replay = tmp_path / "run.mirai-replay"
    arena = tmp_path / "arena.json"
    matrix = tmp_path / "matrix.json"
    report = tmp_path / "report.json"
    evidence = tmp_path / "report.mirai-evidence"
    report.write_text('{"status":"passed"}\n', encoding="utf-8")

    assert main(["passport", "create", str(dummy_model), "--output", str(passport)]) == 0
    assert main(["passport", "verify", str(passport), str(dummy_model)]) == 0
    assert (
        main(
            [
                "replay",
                "capture",
                str(dummy_model),
                "--output",
                str(replay),
                "--capture-inputs",
                "--input",
                "4.0",
            ]
        )
        == 0
    )
    assert main(["replay", "run", str(replay), str(dummy_model)]) == 0
    assert (
        main(
            [
                "arena",
                "run",
                str(dummy_model),
                "--output",
                str(arena),
                "--runs",
                "2",
                "--warmup",
                "0",
            ]
        )
        == 0
    )
    assert main(["arena", "verify", str(arena), "--artifact", str(dummy_model)]) == 0
    assert (
        main(
            [
                "compatibility",
                "add",
                str(arena),
                "--matrix",
                str(matrix),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "evidence",
                "create",
                str(report),
                "--output",
                str(evidence),
                "--artifact",
                str(dummy_model),
                "--passport",
                str(passport),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "evidence",
                "verify",
                str(evidence),
                "--artifact",
                str(dummy_model),
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "Model Passport válido" in output
    assert '"passed": true' in output
    assert "Resultado da Arena válido" in output
    assert "Compatibility Matrix" in output
    assert "Evidence Bundle íntegro" in output


def test_cli_failure_lab_and_hardware(
    dummy_model: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "lab.json"
    private = tmp_path / "lab.key"
    generate_signing_key(private, tmp_path / "lab.pub")

    assert main(["hardware", "--category", "experimental"]) == 0
    assert (
        main(
            [
                "failure-lab",
                str(dummy_model),
                "--output",
                str(output),
                "--benchmark-runs",
                "1",
                "--sign-key",
                str(private),
            ]
        )
        == 0
    )

    captured = capsys.readouterr().out
    assert '"hostname_collected": false' in captured
    assert "CPUExecutionProvider" in captured
    assert output.with_name(output.name + ".sig").is_file()
    assert "Assinatura DSSE" in captured
    assert "Status: passed" in captured
