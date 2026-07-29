"""Testes do formato reproduzível e verificável .mirai."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from mirai.errors import MiraiRuntimeError
from mirai.inspect import validate_artifact
from mirai.package import (
    MANIFEST_MEMBER,
    MODEL_MEMBER,
    create_mirai_package,
    load_mirai_package,
)
from mirai.runtime import prepare_inference, run_model


def _rewrite_package(
    source: Path,
    target: Path,
    *,
    manifest_update: object | None = None,
    extra_member: tuple[str, bytes] | None = None,
) -> None:
    with zipfile.ZipFile(source, "r") as archive:
        manifest = json.loads(archive.read(MANIFEST_MEMBER))
        model = archive.read(MODEL_MEMBER)
    if callable(manifest_update):
        manifest_update(manifest)
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(
            MANIFEST_MEMBER,
            json.dumps(manifest, sort_keys=True).encode("utf-8"),
        )
        archive.writestr(MODEL_MEMBER, model)
        if extra_member is not None:
            archive.writestr(*extra_member)


def test_package_is_reproducible_valid_and_executable(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.mirai"
    second_path = tmp_path / "second.mirai"

    first = create_mirai_package(
        dummy_model,
        first_path,
        name="dummy",
        package_version="1.2.3",
        description="Pacote de teste",
    )
    second = create_mirai_package(
        dummy_model,
        second_path,
        name="dummy",
        package_version="1.2.3",
        description="Pacote de teste",
    )
    result, _ = run_model(first_path, ["5.0"])

    assert first.sha256 == second.sha256
    assert first_path.read_bytes() == second_path.read_bytes()
    assert validate_artifact(first_path) > 0
    assert result == 6.0
    assert first.manifest["inputs"][0]["preprocessing"] == {"kind": "tensor"}
    with zipfile.ZipFile(first_path, "r") as archive:
        assert {item.filename for item in archive.infolist()} == {
            MANIFEST_MEMBER,
            MODEL_MEMBER,
        }
        assert all(
            item.date_time == (1980, 1, 1, 0, 0, 0)
            for item in archive.infolist()
        )


def test_package_rejects_unsafe_identity_before_creating_output(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "should-not-exist.mirai"

    with pytest.raises(MiraiRuntimeError, match="nome do pacote inválido"):
        create_mirai_package(
            dummy_model,
            output,
            name="../../escape",
            package_version="1.0.0",
        )

    assert not output.exists()


def test_package_rejects_noncanonical_semver(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    with pytest.raises(MiraiRuntimeError, match="SemVer"):
        create_mirai_package(
            dummy_model,
            tmp_path / "invalid.mirai",
            name="dummy",
            package_version="1.0.0-01",
        )


def test_package_sanitizes_the_original_model_name(
    dummy_model: Path,
    tmp_path: Path,
) -> None:
    renamed_model = tmp_path / "model with space.onnx"
    renamed_model.write_bytes(dummy_model.read_bytes())
    output = tmp_path / "safe.mirai"

    package = create_mirai_package(
        renamed_model,
        output,
        name="safe",
        package_version="1.0.0",
    )

    assert package.model_name == "model-with-space.onnx"


def test_package_applies_declared_image_preprocessing(
    model_factory: Callable[[str, list[int | str | None], int], Path],
    sample_image: Path,
    tmp_path: Path,
) -> None:
    model = model_factory("vision", [1, 3, 4, 5])
    package_path = tmp_path / "vision.mirai"
    create_mirai_package(
        model,
        package_path,
        name="vision",
        package_version="1.0.0",
        image_input="input",
        layout="nchw",
        mean="[0.5, 0.5, 0.5]",
        std="[0.5, 0.5, 0.5]",
    )

    _, input_feed = prepare_inference(
        package_path,
        [str(sample_image)],
        layout="nhwc",
    )

    tensor = input_feed["input"]
    assert tensor.shape == (1, 3, 4, 5)
    assert np.allclose(tensor, (127 / 255 - 0.5) / 0.5, atol=1e-6)


def test_package_rejects_unknown_members(
    dummy_package: Path,
    tmp_path: Path,
) -> None:
    tampered = tmp_path / "extra.mirai"
    _rewrite_package(
        dummy_package,
        tampered,
        extra_member=("../../escape", b"unsafe"),
    )

    with pytest.raises(MiraiRuntimeError, match="somente manifest.json"):
        load_mirai_package(tampered)


def test_package_rejects_compressed_members(
    dummy_package: Path,
    tmp_path: Path,
) -> None:
    compressed = tmp_path / "compressed.mirai"
    with zipfile.ZipFile(dummy_package, "r") as archive:
        manifest = archive.read(MANIFEST_MEMBER)
        model = archive.read(MODEL_MEMBER)
    with zipfile.ZipFile(
        compressed,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr(MANIFEST_MEMBER, manifest)
        archive.writestr(MODEL_MEMBER, model)

    with pytest.raises(MiraiRuntimeError, match="sem compressão"):
        load_mirai_package(compressed)


def test_package_rejects_duplicate_json_keys(
    dummy_package: Path,
    tmp_path: Path,
) -> None:
    duplicated = tmp_path / "duplicate-key.mirai"
    with zipfile.ZipFile(dummy_package, "r") as archive:
        manifest = archive.read(MANIFEST_MEMBER).decode("utf-8")
        model = archive.read(MODEL_MEMBER)
    manifest = manifest.replace(
        '  "name": "dummy",',
        '  "name": "other",\n  "name": "dummy",',
        1,
    )
    with zipfile.ZipFile(duplicated, "w") as archive:
        archive.writestr(MANIFEST_MEMBER, manifest.encode("utf-8"))
        archive.writestr(MODEL_MEMBER, model)

    with pytest.raises(MiraiRuntimeError, match="campo duplicado: name"):
        load_mirai_package(duplicated)


def test_package_rejects_tampered_model_hash(
    dummy_package: Path,
    tmp_path: Path,
) -> None:
    tampered = tmp_path / "hash.mirai"

    def change_hash(manifest: dict[str, object]) -> None:
        model = manifest["model"]
        assert isinstance(model, dict)
        model["sha256"] = "0" * 64

    _rewrite_package(
        dummy_package,
        tampered,
        manifest_update=change_hash,
    )

    with pytest.raises(MiraiRuntimeError, match="SHA-256 do modelo"):
        load_mirai_package(tampered)


def test_package_rejects_contract_that_does_not_match_onnx(
    dummy_package: Path,
    tmp_path: Path,
) -> None:
    tampered = tmp_path / "contract.mirai"

    def change_contract(manifest: dict[str, object]) -> None:
        inputs = manifest["inputs"]
        assert isinstance(inputs, list)
        assert isinstance(inputs[0], dict)
        inputs[0]["name"] = "different"

    _rewrite_package(
        dummy_package,
        tampered,
        manifest_update=change_contract,
    )

    with pytest.raises(MiraiRuntimeError, match="contrato de entradas"):
        validate_artifact(tampered)
