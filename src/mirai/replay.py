"""Captura explícita e reprodução local de uma inferência MiraiOS."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from . import __version__
from .attachments import encode_remote_inputs, materialize_remote_inputs
from .errors import MiraiRuntimeError
from .inspect import validate_artifact
from .json_codec import canonical_json_bytes, strict_json_dumps, strict_json_loads
from .providers import normalize_provider_profile
from .runtime import run_model
from .storage import atomic_write_text, stable_file_digest

REPLAY_FORMAT = "mirai.replay-bundle"
REPLAY_VERSION = 1
REPLAY_EXTENSION = ".mirai-replay"
MAX_REPLAY_BYTES = 16 * 1024 * 1024
MAX_REPLAY_INPUTS = 64
MAX_TOLERANCE = 1.0


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _validate_tolerance(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= MAX_TOLERANCE
    ):
        raise MiraiRuntimeError("tolerância do Replay deve estar entre 0 e 1")
    return float(value)


def _results_match(actual: Any, expected: Any, tolerance: float) -> bool:
    if (
        isinstance(actual, (int, float))
        and not isinstance(actual, bool)
        and isinstance(expected, (int, float))
        and not isinstance(expected, bool)
    ):
        return math.isclose(
            float(actual),
            float(expected),
            rel_tol=tolerance,
            abs_tol=tolerance,
        )
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _results_match(left, right, tolerance)
            for left, right in zip(actual, expected, strict=True)
        )
    if isinstance(actual, dict) and isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _results_match(actual[key], expected[key], tolerance)
            for key in actual
        )
    return actual == expected


def create_replay_bundle(
    artifact_path: Path,
    output_path: Path,
    *,
    input_specs: list[str] | None = None,
    layout: str = "auto",
    provider_profile: str = "auto",
    tolerance: float = 1e-6,
    capture_inputs: bool = False,
    replace: bool = False,
) -> dict[str, Any]:
    """Executa uma inferência e captura as entradas somente com consentimento."""
    if not capture_inputs:
        raise MiraiRuntimeError(
            "o Replay pode conter dados sensíveis; confirme com --capture-inputs"
        )
    if input_specs is not None and len(input_specs) > MAX_REPLAY_INPUTS:
        raise MiraiRuntimeError(
            f"o Replay aceita no máximo {MAX_REPLAY_INPUTS} entradas"
        )
    if layout not in {"auto", "nchw", "nhwc"}:
        raise MiraiRuntimeError("layout do Replay inválido")
    normalized_provider = normalize_provider_profile(provider_profile)
    tolerance_value = _validate_tolerance(tolerance)
    artifact = artifact_path.expanduser().resolve()
    output = output_path.expanduser().resolve()
    if output.suffix.lower() != REPLAY_EXTENSION:
        raise MiraiRuntimeError(
            f"o Replay deve usar a extensão {REPLAY_EXTENSION}"
        )
    if output.exists() and not replace:
        raise MiraiRuntimeError(f"arquivo de saída já existe: {output}")

    validate_artifact(artifact)
    artifact_digest, artifact_size = stable_file_digest(artifact)
    rewritten_inputs, attachments = encode_remote_inputs(input_specs)
    with materialize_remote_inputs(rewritten_inputs, attachments) as captured_inputs:
        result, _ = run_model(
            artifact,
            captured_inputs,
            layout,
            provider_profile=normalized_provider,
        )
    replay: dict[str, Any] = {
        "format": REPLAY_FORMAT,
        "schema_version": REPLAY_VERSION,
        "subject": {
            "name": artifact.name,
            "sha256": artifact_digest,
            "size_bytes": artifact_size,
        },
        "execution": {
            "layout": layout,
            "provider_profile": normalized_provider,
            "input_specs": rewritten_inputs,
            "attachments": attachments,
        },
        "expected": {
            "result": _json_safe(result),
            "tolerance": tolerance_value,
            "comparison": "recursive-numeric-isclose",
        },
        "privacy": {
            "capture_was_explicit": True,
            "inputs_included": bool(input_specs),
            "uses_runtime_default": not input_specs,
            "local_paths_included": False,
        },
        "limitations": [
            "o bundle pode conter entradas sensíveis e deve ser protegido",
            "saídas grandes usam a representação normalizada do MiraiOS",
            "a reprodução exige exatamente o artefato identificado por SHA-256",
        ],
        "created_by": {"tool": "miraios", "version": __version__},
    }
    replay["replay_id"] = "replay-" + hashlib.sha256(
        canonical_json_bytes(replay)
    ).hexdigest()[:24]
    try:
        atomic_write_text(
            output,
            strict_json_dumps(replay, pretty=True, sort_keys=True) + "\n",
            mode=0o600,
        )
    except OSError as error:
        raise MiraiRuntimeError(f"não foi possível gravar o Replay: {error}") from error
    return replay


def load_replay_bundle(path: Path) -> dict[str, Any]:
    target = path.expanduser().resolve()
    if target.suffix.lower() != REPLAY_EXTENSION:
        raise MiraiRuntimeError(f"o Replay deve usar a extensão {REPLAY_EXTENSION}")
    try:
        size = target.stat().st_size
        content = target.read_bytes()
    except OSError as error:
        raise MiraiRuntimeError(f"não foi possível ler o Replay: {error}") from error
    if not 0 < size <= MAX_REPLAY_BYTES:
        raise MiraiRuntimeError("Replay vazio ou acima de 16 MB")
    replay = strict_json_loads(
        content,
        label="Replay",
        max_string_chars=MAX_REPLAY_BYTES,
    )
    if not isinstance(replay, dict):
        raise MiraiRuntimeError("Replay deve ser um objeto JSON")
    required = {
        "format",
        "schema_version",
        "subject",
        "execution",
        "expected",
        "privacy",
        "limitations",
        "created_by",
        "replay_id",
    }
    if set(replay) != required:
        raise MiraiRuntimeError("Replay possui campos incompatíveis")
    if replay["format"] != REPLAY_FORMAT or replay["schema_version"] != REPLAY_VERSION:
        raise MiraiRuntimeError("versão do Replay não suportada")
    subject = replay.get("subject")
    execution = replay.get("execution")
    expected = replay.get("expected")
    if not isinstance(subject, dict) or set(subject) != {"name", "sha256", "size_bytes"}:
        raise MiraiRuntimeError("subject do Replay inválido")
    subject_name = subject.get("name")
    subject_digest = subject.get("sha256")
    subject_size = subject.get("size_bytes")
    if (
        not isinstance(subject_name, str)
        or not subject_name
        or Path(subject_name).name != subject_name
        or not isinstance(subject_digest, str)
        or len(subject_digest) != 64
        or any(character not in "0123456789abcdef" for character in subject_digest)
        or isinstance(subject_size, bool)
        or not isinstance(subject_size, int)
        or subject_size <= 0
    ):
        raise MiraiRuntimeError("identidade do artefato no Replay inválida")
    if not isinstance(execution, dict) or set(execution) != {
        "layout",
        "provider_profile",
        "input_specs",
        "attachments",
    }:
        raise MiraiRuntimeError("execução do Replay inválida")
    input_specs = execution.get("input_specs")
    if input_specs is not None and (
        not isinstance(input_specs, list)
        or len(input_specs) > MAX_REPLAY_INPUTS
        or any(not isinstance(item, str) for item in input_specs)
    ):
        raise MiraiRuntimeError("entradas do Replay inválidas")
    if execution.get("layout") not in {"auto", "nchw", "nhwc"}:
        raise MiraiRuntimeError("layout do Replay inválido")
    provider_profile = execution.get("provider_profile")
    if not isinstance(provider_profile, str):
        raise MiraiRuntimeError("provider profile do Replay inválido")
    normalize_provider_profile(provider_profile)
    if not isinstance(expected, dict) or set(expected) != {
        "result",
        "tolerance",
        "comparison",
    }:
        raise MiraiRuntimeError("resultado esperado do Replay inválido")
    expected["tolerance"] = _validate_tolerance(expected.get("tolerance"))
    if expected.get("comparison") != "recursive-numeric-isclose":
        raise MiraiRuntimeError("comparação do Replay não suportada")
    privacy = replay.get("privacy")
    if not isinstance(privacy, dict) or set(privacy) != {
        "capture_was_explicit",
        "inputs_included",
        "uses_runtime_default",
        "local_paths_included",
    }:
        raise MiraiRuntimeError("claims de privacidade do Replay inválidos")
    if (
        privacy.get("capture_was_explicit") is not True
        or privacy.get("local_paths_included") is not False
        or privacy.get("inputs_included") is not bool(input_specs)
        or privacy.get("uses_runtime_default") is not (not input_specs)
    ):
        raise MiraiRuntimeError("claims de privacidade do Replay não conferem")
    claimed_id = replay["replay_id"]
    without_id = dict(replay)
    del without_id["replay_id"]
    expected_id = "replay-" + hashlib.sha256(
        canonical_json_bytes(without_id)
    ).hexdigest()[:24]
    if claimed_id != expected_id:
        raise MiraiRuntimeError("identificador do Replay não corresponde ao conteúdo")
    with materialize_remote_inputs(input_specs, execution.get("attachments")):
        pass
    return replay


def run_replay(bundle_path: Path, artifact_path: Path) -> dict[str, Any]:
    """Verifica a identidade, materializa entradas e compara a nova execução."""
    replay = load_replay_bundle(bundle_path)
    artifact = artifact_path.expanduser().resolve()
    validate_artifact(artifact)
    digest, size = stable_file_digest(artifact)
    subject = replay["subject"]
    if digest != subject["sha256"] or size != subject["size_bytes"]:
        raise MiraiRuntimeError("o artefato não corresponde ao Replay")
    execution = replay["execution"]
    with materialize_remote_inputs(
        execution["input_specs"],
        execution["attachments"],
    ) as input_specs:
        result, elapsed_ms = run_model(
            artifact,
            input_specs,
            execution["layout"],
            provider_profile=execution["provider_profile"],
        )
    actual = _json_safe(result)
    expected = replay["expected"]
    passed = _results_match(actual, expected["result"], expected["tolerance"])
    return {
        "format": "mirai.replay-result",
        "schema_version": 1,
        "replay_id": replay["replay_id"],
        "passed": passed,
        "expected": expected["result"],
        "actual": actual,
        "tolerance": expected["tolerance"],
        "latency_ms": elapsed_ms,
    }
