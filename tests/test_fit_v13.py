"""Testes da geração controlada de variantes INT8 pelo Mirai Fit v1."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper

import mirai.fit as fit_module
from mirai.benchmark import BenchmarkStats
from mirai.errors import MiraiRuntimeError
from mirai.fit import _compare_outputs, _publish_outputs, fit_model
from mirai.package import load_mirai_package
from mirai.signing import generate_signing_key, verify_artifact


def _quantizable_model(path: Path) -> Path:
    model_input = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 4])
    model_output = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 3])
    weights = numpy_helper.from_array(
        np.array(
            [
                [0.13, -0.27, 0.51],
                [0.72, 0.19, -0.33],
                [-0.44, 0.61, 0.08],
                [0.37, -0.58, 0.91],
            ],
            dtype=np.float32,
        ),
        name="weights",
    )
    node = helper.make_node("MatMul", ["input", "weights"], ["output"])
    graph = helper.make_graph(
        [node],
        "QuantizableGraph",
        [model_input],
        [model_output],
        [weights],
    )
    model = helper.make_model(
        graph,
        producer_name="MiraiOSFitTests",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    model.ir_version = 10
    onnx.checker.check_model(model)
    onnx.save(model, path)
    return path


def _stats(p95: float) -> BenchmarkStats:
    return BenchmarkStats(
        runs=1,
        warmup_runs=0,
        total_ms=p95,
        average_ms=p95,
        median_ms=p95,
        p95_ms=p95,
        min_ms=p95,
        max_ms=p95,
        inferences_per_second=1000 / p95 if p95 else float("inf"),
    )


def test_fit_accepts_validated_variant_and_publishes_evidence(tmp_path: Path) -> None:
    source = _quantizable_model(tmp_path / "source.onnx")
    output = tmp_path / "model-int8.mirai"

    outcome = fit_model(
        source,
        output,
        name="edge-int8",
        package_version="1.0.0",
        input_specs=["[[1.0,2.0,3.0,4.0]]"],
        runs=1,
        warmup_runs=0,
        max_absolute_error=1.0,
        min_speedup=0,
    )
    package = load_mirai_package(output)
    report = json.loads(outcome.report_path.read_text(encoding="utf-8"))

    assert outcome.accepted is True
    assert outcome.package_path == output
    assert package.name == "edge-int8"
    assert report["status"] == "accepted"
    assert report["checks"] == {"quality_passed": True, "performance_passed": True}
    assert report["candidate"]["method"] == "onnxruntime-dynamic-int8"
    assert report["hardware"]["profile"]
    assert report["quality"]["compared_values"] == 3


def test_fit_rejection_writes_report_but_never_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _quantizable_model(tmp_path / "source.onnx")
    output = tmp_path / "rejected.mirai"
    monkeypatch.setattr(
        fit_module,
        "_compare_outputs",
        lambda *_: {
            "compared_values": 3,
            "max_absolute_error": 2.0,
            "max_relative_error": 2.0,
        },
    )

    outcome = fit_model(
        source,
        output,
        name="rejected",
        package_version="1.0.0",
        runs=1,
        warmup_runs=0,
        max_absolute_error=0.1,
        min_speedup=0,
    )

    assert outcome.accepted is False
    assert outcome.package_path is None
    assert not output.exists()
    assert outcome.report_path.exists()
    assert outcome.report["status"] == "rejected"
    assert outcome.report["candidate"]["package"] is None


def test_fit_performance_gate_can_reject_fast_quality_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _quantizable_model(tmp_path / "source.onnx")
    measured = iter([_stats(1.0), _stats(2.0)])
    monkeypatch.setattr(fit_module, "benchmark_session", lambda *_: next(measured))

    outcome = fit_model(
        source,
        tmp_path / "slow.mirai",
        name="slow",
        package_version="1.0.0",
        runs=1,
        warmup_runs=0,
        max_absolute_error=1.0,
        min_speedup=1.0,
    )

    assert outcome.accepted is False
    assert outcome.report["checks"]["quality_passed"] is True
    assert outcome.report["checks"]["performance_passed"] is False
    assert outcome.report["benchmark"]["p95_speedup"] == 0.5


def test_fit_signs_only_accepted_package(tmp_path: Path) -> None:
    source = _quantizable_model(tmp_path / "source.onnx")
    private_key = tmp_path / "fit-key.pem"
    keys = generate_signing_key(private_key)
    output = tmp_path / "signed.mirai"

    outcome = fit_model(
        source,
        output,
        name="signed",
        package_version="1.0.0",
        runs=1,
        warmup_runs=0,
        max_absolute_error=1.0,
        min_speedup=0,
        signing_key_path=private_key,
    )
    assert outcome.signature_path is not None
    verified = verify_artifact(output, outcome.signature_path, Path(keys["public_key"]))

    assert verified["valid"] is True
    assert outcome.report["candidate"]["signature"] == str(outcome.signature_path)


def test_fit_replace_keeps_previous_package_when_candidate_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _quantizable_model(tmp_path / "source.onnx")
    output = tmp_path / "known-good.mirai"
    output.write_bytes(b"previous-known-good")
    monkeypatch.setattr(
        fit_module,
        "_compare_outputs",
        lambda *_: {
            "compared_values": 1,
            "max_absolute_error": 99.0,
            "max_relative_error": 99.0,
        },
    )

    outcome = fit_model(
        source,
        output,
        name="known-good",
        package_version="2.0.0",
        runs=1,
        warmup_runs=0,
        max_absolute_error=0,
        min_speedup=0,
        replace=True,
    )

    assert outcome.accepted is False
    assert output.read_bytes() == b"previous-known-good"
    assert outcome.report_path.exists()


def test_fit_unsigned_replace_removes_stale_signature(tmp_path: Path) -> None:
    source = _quantizable_model(tmp_path / "source.onnx")
    output = tmp_path / "replace.mirai"
    output.write_bytes(b"old")
    stale_signature = output.with_name(output.name + ".dsse.json")
    stale_signature.write_text("old signature", encoding="utf-8")

    outcome = fit_model(
        source,
        output,
        name="replace",
        package_version="2.0.0",
        runs=1,
        warmup_runs=0,
        max_absolute_error=1.0,
        min_speedup=0,
        replace=True,
    )

    assert outcome.accepted is True
    assert load_mirai_package(output).version == "2.0.0"
    assert not stale_signature.exists()


def test_fit_publish_restores_previous_files_on_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "staged-first"
    second = tmp_path / "staged-second"
    first.write_text("new first", encoding="utf-8")
    second.write_text("new second", encoding="utf-8")
    target_first = tmp_path / "first"
    target_second = tmp_path / "second"
    target_first.write_text("old first", encoding="utf-8")
    target_second.write_text("old second", encoding="utf-8")
    original_replace = os.replace
    calls = 0

    def fail_once(source: Path | str, target: Path | str) -> None:
        nonlocal calls
        calls += 1
        if calls == 4:
            raise OSError("simulated commit failure")
        original_replace(source, target)

    monkeypatch.setattr(fit_module.os, "replace", fail_once)

    with pytest.raises(MiraiRuntimeError, match="publicar"):
        _publish_outputs(
            [(first, target_first), (second, target_second)],
            replace=True,
        )

    assert target_first.read_text(encoding="utf-8") == "old first"
    assert target_second.read_text(encoding="utf-8") == "old second"


@pytest.mark.parametrize(
    "changes",
    [
        {"runs": 0},
        {"warmup_runs": -1},
        {"max_absolute_error": -1.0},
        {"max_absolute_error": float("nan")},
        {"min_speedup": -1.0},
        {"min_speedup": float("inf")},
        {"output_suffix": ".onnx"},
    ],
)
def test_fit_rejects_invalid_policy_or_output(
    tmp_path: Path,
    changes: dict[str, Any],
) -> None:
    source = _quantizable_model(tmp_path / "source.onnx")
    suffix = changes.pop("output_suffix", ".mirai")
    kwargs: dict[str, Any] = {
        "name": "invalid",
        "package_version": "1.0.0",
        "runs": 1,
        "warmup_runs": 0,
    }
    kwargs.update(changes)

    with pytest.raises(MiraiRuntimeError):
        fit_model(source, tmp_path / f"output{suffix}", **kwargs)


def test_fit_wraps_quantizer_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _quantizable_model(tmp_path / "source.onnx")
    monkeypatch.setattr(
        fit_module,
        "_quantize_dynamic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MiraiRuntimeError("quantizer failed")),
    )

    with pytest.raises(MiraiRuntimeError, match="quantizer failed"):
        fit_model(
            source,
            tmp_path / "output.mirai",
            name="failure",
            package_version="1.0.0",
            runs=1,
            warmup_runs=0,
        )


def test_output_comparison_rejects_shape_count_and_non_finite_values() -> None:
    with pytest.raises(MiraiRuntimeError, match="quantidade"):
        _compare_outputs([np.array([1.0])], [], np)
    with pytest.raises(MiraiRuntimeError, match="shape"):
        _compare_outputs([np.array([1.0])], [np.array([[1.0]])], np)
    with pytest.raises(MiraiRuntimeError, match="não finita"):
        _compare_outputs([np.array([1.0])], [np.array([np.nan])], np)
    with pytest.raises(MiraiRuntimeError, match="numéricas"):
        _compare_outputs([np.array(["text"])], [np.array(["text"])], np)


def test_output_comparison_reports_absolute_and_relative_error() -> None:
    compared = _compare_outputs(
        [np.array([1.0, 2.0])],
        [np.array([1.1, 1.5])],
        np,
    )

    assert compared["compared_values"] == 2
    assert compared["max_absolute_error"] == pytest.approx(0.5)
    assert compared["max_relative_error"] == pytest.approx(0.25)


def test_publish_outputs_rejects_empty_duplicate_and_existing_targets(tmp_path: Path) -> None:
    _publish_outputs([], replace=False)
    source = tmp_path / "source"
    source.write_text("new", encoding="utf-8")
    target = tmp_path / "target"

    with pytest.raises(MiraiRuntimeError, match="duplicados"):
        _publish_outputs([(source, target), (source, target)], replace=False)

    target.write_text("old", encoding="utf-8")
    with pytest.raises(MiraiRuntimeError, match="já existe"):
        _publish_outputs([(source, target)], replace=False)


def test_fit_rejects_non_onnx_source_and_existing_outputs(tmp_path: Path) -> None:
    with pytest.raises(MiraiRuntimeError, match="origem"):
        fit_model(
            tmp_path / "model.bin",
            tmp_path / "output.mirai",
            name="invalid",
            package_version="1.0.0",
        )

    source = _quantizable_model(tmp_path / "source.onnx")
    output = tmp_path / "exists.mirai"
    output.touch()
    with pytest.raises(MiraiRuntimeError, match="já existe"):
        fit_model(source, output, name="exists", package_version="1.0.0")

    output.unlink()
    report = output.with_name(output.name + ".fit.json")
    report.touch()
    with pytest.raises(MiraiRuntimeError, match="relatório"):
        fit_model(source, output, name="exists", package_version="1.0.0")


def test_fit_wraps_report_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _quantizable_model(tmp_path / "source.onnx")
    monkeypatch.setattr(
        fit_module,
        "atomic_write_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(MiraiRuntimeError, match="relatório"):
        fit_model(
            source,
            tmp_path / "output.mirai",
            name="write-failure",
            package_version="1.0.0",
            runs=1,
            warmup_runs=0,
            max_absolute_error=1.0,
            min_speedup=0,
        )

    assert not (tmp_path / "output.mirai").exists()
