"""Testes dos anexos remotos efêmeros e não confiáveis."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mirai.attachments import encode_remote_inputs, materialize_remote_inputs
from mirai.errors import MiraiRuntimeError


def test_encode_image_replaces_local_path_with_reference(sample_image: Path) -> None:
    specs, attachments = encode_remote_inputs([f"image={sample_image}"])

    assert specs is not None
    assert specs[0].startswith("image=@attachment:")
    assert attachments[0]["name"] == sample_image.name
    assert attachments[0]["media_type"] == "image/png"
    assert attachments[0]["size_bytes"] == sample_image.stat().st_size


def test_encode_keeps_numeric_inputs_without_attachments() -> None:
    assert encode_remote_inputs(["x=[1,2,3]"]) == (["x=[1,2,3]"], [])


def test_encode_rejects_missing_supported_file(tmp_path: Path) -> None:
    with pytest.raises(MiraiRuntimeError, match="não encontrado"):
        encode_remote_inputs([str(tmp_path / "missing.png")])


def test_encode_rejects_empty_file(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.touch()
    with pytest.raises(MiraiRuntimeError, match="vazio"):
        encode_remote_inputs([str(empty)])


def test_materialize_image_uses_random_temporary_directory(sample_image: Path) -> None:
    specs, attachments = encode_remote_inputs([str(sample_image)])
    assert specs is not None

    with materialize_remote_inputs(specs, attachments) as resolved:
        assert resolved is not None
        path = Path(resolved[0])
        assert path.is_file()
        assert path.name.endswith(".png")
        assert path != sample_image
    assert not path.exists()


def test_materialize_json_turns_file_into_literal(tmp_path: Path) -> None:
    source = tmp_path / "input.json"
    source.write_text('{"values": [1, 2]}', encoding="utf-8")
    specs, attachments = encode_remote_inputs([f"input={source}"])

    with materialize_remote_inputs(specs, attachments) as resolved:
        assert resolved == ['input={"values":[1,2]}']


def test_materialize_npy_validates_without_pickle(tmp_path: Path) -> None:
    source = tmp_path / "input.npy"
    np.save(source, np.array([1.0, 2.0], dtype=np.float32))
    specs, attachments = encode_remote_inputs([str(source)])

    with materialize_remote_inputs(specs, attachments) as resolved:
        assert resolved is not None
        loaded = np.load(resolved[0], allow_pickle=False)
        assert loaded.tolist() == [1.0, 2.0]


def test_materialize_rejects_object_npy(tmp_path: Path) -> None:
    source = tmp_path / "objects.npy"
    np.save(source, np.array([{"unsafe": True}], dtype=object))
    specs, attachments = encode_remote_inputs([str(source)])

    with pytest.raises(MiraiRuntimeError, match="inseguro|objetos"):
        with materialize_remote_inputs(specs, attachments):
            pass


def test_materialize_rejects_path_traversal_name(sample_image: Path) -> None:
    specs, attachments = encode_remote_inputs([str(sample_image)])
    attachments[0]["name"] = "../escape.png"

    with pytest.raises(MiraiRuntimeError, match="nome de anexo"):
        with materialize_remote_inputs(specs, attachments):
            pass


def test_materialize_rejects_mime_extension_mismatch(sample_image: Path) -> None:
    specs, attachments = encode_remote_inputs([str(sample_image)])
    attachments[0]["media_type"] = "image/jpeg"

    with pytest.raises(MiraiRuntimeError, match="tipo de mídia"):
        with materialize_remote_inputs(specs, attachments):
            pass


def test_materialize_rejects_invalid_base64(sample_image: Path) -> None:
    specs, attachments = encode_remote_inputs([str(sample_image)])
    attachments[0]["data"] = "%%%"

    with pytest.raises(MiraiRuntimeError, match="base64"):
        with materialize_remote_inputs(specs, attachments):
            pass


def test_materialize_rejects_wrong_hash(sample_image: Path) -> None:
    specs, attachments = encode_remote_inputs([str(sample_image)])
    attachments[0]["sha256"] = "0" * 64

    with pytest.raises(MiraiRuntimeError, match="SHA-256"):
        with materialize_remote_inputs(specs, attachments):
            pass


def test_materialize_rejects_wrong_size(sample_image: Path) -> None:
    specs, attachments = encode_remote_inputs([str(sample_image)])
    attachments[0]["size_bytes"] += 1

    with pytest.raises(MiraiRuntimeError, match="tamanho real"):
        with materialize_remote_inputs(specs, attachments):
            pass


def test_materialize_rejects_spoofed_image(tmp_path: Path) -> None:
    fake = tmp_path / "fake.png"
    fake.write_bytes(b"this is not a png")
    specs, attachments = encode_remote_inputs([str(fake)])

    with pytest.raises(MiraiRuntimeError, match="imagem inválido"):
        with materialize_remote_inputs(specs, attachments):
            pass


def test_materialize_rejects_non_finite_json(tmp_path: Path) -> None:
    source = tmp_path / "input.json"
    source.write_text("NaN", encoding="utf-8")
    specs, attachments = encode_remote_inputs([str(source)])

    with pytest.raises(MiraiRuntimeError, match="JSON inválido"):
        with materialize_remote_inputs(specs, attachments):
            pass


def test_materialize_rejects_duplicate_attachment_id(sample_image: Path) -> None:
    specs, attachments = encode_remote_inputs([str(sample_image)])
    attachments.append(dict(attachments[0]))

    with pytest.raises(MiraiRuntimeError, match="duplicado"):
        with materialize_remote_inputs(specs, attachments):
            pass


def test_materialize_rejects_unreferenced_attachment(sample_image: Path) -> None:
    _, attachments = encode_remote_inputs([str(sample_image)])

    with pytest.raises(MiraiRuntimeError, match="não foram referenciados"):
        with materialize_remote_inputs(["5.0"], attachments):
            pass


def test_materialize_rejects_missing_attachment_reference() -> None:
    with pytest.raises(MiraiRuntimeError, match="anexo ausente"):
        with materialize_remote_inputs(["@attachment:0000000000000000"], []):
            pass


@pytest.mark.parametrize("path", ["/etc/secret.png", "C:\\secret.npy", "local.json"])
def test_materialize_never_reads_agent_local_paths(path: str) -> None:
    with pytest.raises(MiraiRuntimeError, match="anexos validados"):
        with materialize_remote_inputs([path], []):
            pass


def test_materialize_rejects_unknown_descriptor_field(sample_image: Path) -> None:
    specs, attachments = encode_remote_inputs([str(sample_image)])
    attachments[0]["command"] = "run"

    with pytest.raises(MiraiRuntimeError, match="campos incompatíveis"):
        with materialize_remote_inputs(specs, attachments):
            pass


def test_materialize_rejects_content_that_does_not_match_declared_image(
    sample_image: Path,
) -> None:
    specs, attachments = encode_remote_inputs([str(sample_image)])
    attachments[0]["name"] = "renamed.jpg"
    attachments[0]["media_type"] = "image/jpeg"

    with pytest.raises(MiraiRuntimeError, match="não corresponde"):
        with materialize_remote_inputs(specs, attachments):
            pass


def test_materialize_rejects_image_pixel_bomb(
    sample_image: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    specs, attachments = encode_remote_inputs([str(sample_image)])
    monkeypatch.setattr("mirai.attachments.MAX_IMAGE_PIXELS", 10)

    with pytest.raises(MiraiRuntimeError, match="milhões de pixels"):
        with materialize_remote_inputs(specs, attachments):
            pass
