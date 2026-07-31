"""Testes de propriedades reproduzíveis para invariantes de fronteira."""

from __future__ import annotations

import re
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from mirai.agent import AgentRequestError, _safe_artifact_name
from mirai.json_codec import canonical_json_bytes, strict_json_dumps, strict_json_loads
from mirai.security import PAIRING_ALPHABET, format_pairing_code, normalize_pairing_code
from mirai.storage import stable_file_digest, verify_file

PROPERTY_SETTINGS = settings(
    max_examples=200,
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
JSON_SCALARS = st.none() | st.booleans() | st.integers() | st.text(max_size=64)
JSON_VALUES = st.recursive(
    JSON_SCALARS,
    lambda children: st.lists(children, max_size=8)
    | st.dictionaries(st.text(max_size=32), children, max_size=8),
    max_leaves=30,
)


@PROPERTY_SETTINGS
@given(JSON_VALUES)
def test_strict_json_round_trip(value: object) -> None:
    assert strict_json_loads(strict_json_dumps(value)) == value


@PROPERTY_SETTINGS
@given(JSON_VALUES)
def test_canonical_json_is_idempotent(value: object) -> None:
    encoded = canonical_json_bytes(value)
    assert canonical_json_bytes(strict_json_loads(encoded)) == encoded


@PROPERTY_SETTINGS
@given(st.text(max_size=256))
def test_artifact_sanitizer_never_emits_paths(raw: str) -> None:
    try:
        safe = _safe_artifact_name(f"{raw}model.onnx")
    except AgentRequestError:
        return
    assert SAFE_NAME.fullmatch(safe)
    assert safe.endswith(".onnx")
    assert "/" not in safe and "\\" not in safe
    assert len(safe) <= 128


@PROPERTY_SETTINGS
@given(st.text(alphabet=PAIRING_ALPHABET, min_size=12, max_size=12))
def test_pairing_format_round_trip(code: str) -> None:
    formatted = format_pairing_code(code)
    assert normalize_pairing_code(formatted.lower()) == code


@settings(
    max_examples=100,
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(st.binary(max_size=4096))
def test_file_digest_verifies_exact_bytes(tmp_path: Path, content: bytes) -> None:
    target = tmp_path / "artifact.bin"
    target.write_bytes(content)
    digest, size = stable_file_digest(target)
    verify_file(
        target,
        expected_sha256=digest,
        expected_size=size,
        label="artifact",
    )
