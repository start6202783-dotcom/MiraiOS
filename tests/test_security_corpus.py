"""Corpus determinístico: 1.024 casos hostis em quatro fronteiras distintas."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mirai.admission import AdmissionPolicy, admit_artifact
from mirai.agent import _safe_artifact_name
from mirai.errors import MiraiRuntimeError
from mirai.json_codec import strict_json_loads
from mirai.security import PairingDenied, normalize_pairing_code

CORPUS_SIZE = 256
SAFE_ARTIFACT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


JSON_DUPLICATES = [
    f'{{"field_{index}":0,"nested":{{"ok":true}},"field_{index}":1}}'
    for index in range(CORPUS_SIZE)
]


HOSTILE_PREFIXES = (
    "../",
    "..\\",
    "/tmp/",
    "C:\\temp\\",
    "$()",
    "`id`",
    ";rm",
    "|pipe",
    "&job",
    "<input",
    ">output",
    "'quote",
    '"quote',
    "\u202ereverse",
    "💥",
    "\x1b[31m",
)
HOSTILE_ARTIFACT_NAMES = [
    f"{HOSTILE_PREFIXES[index % len(HOSTILE_PREFIXES)]}model-{index}.onnx"
    for index in range(CORPUS_SIZE)
]


INVALID_PAIRING_CHARACTERS = (
    "0",
    "1",
    "I",
    "O",
    "#",
    "/",
    "\\",
    ":",
    "%",
    "@",
    "=",
    "+",
    "*",
    "?",
    "!",
    ".",
    ",",
    "_",
    "~",
    "💥",
)
INVALID_PAIRING_CODES = [
    (
        "A" * (index % 12)
        + INVALID_PAIRING_CHARACTERS[index // 12]
        + "A" * (11 - (index % 12))
    )
    if index < 240
    else "A" * (index - 227)
    for index in range(CORPUS_SIZE)
]


MALFORMED_SIGNATURE_HEADERS = [
    f"{index:04x}%{'A' * (index % 47)}" for index in range(CORPUS_SIZE)
]


@pytest.mark.parametrize(
    "payload",
    JSON_DUPLICATES,
    ids=[f"duplicate-json-{index:03d}" for index in range(CORPUS_SIZE)],
)
def test_corpus_rejects_ambiguous_json(payload: str) -> None:
    with pytest.raises(MiraiRuntimeError, match="duplicada"):
        strict_json_loads(payload)


@pytest.mark.parametrize(
    "raw_name",
    HOSTILE_ARTIFACT_NAMES,
    ids=[f"artifact-name-{index:03d}" for index in range(CORPUS_SIZE)],
)
def test_corpus_sanitizes_hostile_artifact_names(raw_name: str) -> None:
    safe = _safe_artifact_name(raw_name)

    assert SAFE_ARTIFACT_PATTERN.fullmatch(safe)
    assert safe.endswith(".onnx")
    assert "/" not in safe and "\\" not in safe
    assert len(safe) <= 128


@pytest.mark.parametrize(
    "code",
    INVALID_PAIRING_CODES,
    ids=[f"pairing-code-{index:03d}" for index in range(CORPUS_SIZE)],
)
def test_corpus_rejects_invalid_pairing_codes(code: str) -> None:
    with pytest.raises(PairingDenied, match="inválido"):
        normalize_pairing_code(code)


@pytest.mark.parametrize(
    "header",
    MALFORMED_SIGNATURE_HEADERS,
    ids=[f"signature-header-{index:03d}" for index in range(CORPUS_SIZE)],
)
def test_corpus_rejects_malformed_signature_transport(header: str) -> None:
    with pytest.raises(MiraiRuntimeError, match="base64"):
        admit_artifact(
            Path("artifact.mirai"),
            header,
            AdmissionPolicy(),
        )
