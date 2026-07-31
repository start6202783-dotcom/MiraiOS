"""Codificação JSON estrita e limitada para todas as fronteiras do MiraiOS."""

from __future__ import annotations

import json
from typing import Any

from .errors import MiraiRuntimeError

MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000
MAX_JSON_STRING_CHARS = 2 * 1024 * 1024


def _reject_constant(value: str) -> None:
    raise ValueError(f"número não finito: {value}")


def _object_without_duplicates(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"chave JSON duplicada: {key}")
        result[key] = value
    return result


def _validate_limits(
    value: Any,
    *,
    max_depth: int,
    max_nodes: int,
    max_string_chars: int,
) -> None:
    """Percorre sem recursão para limitar custo e profundidade do payload."""
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise ValueError(f"JSON excede o limite de {max_nodes} valores")
        if depth > max_depth:
            raise ValueError(f"JSON excede a profundidade máxima de {max_depth}")
        if isinstance(current, str):
            if len(current) > max_string_chars:
                raise ValueError(
                    "string JSON excede o limite de "
                    f"{max_string_chars} caracteres"
                )
        elif isinstance(current, dict):
            for key, item in current.items():
                if len(key) > max_string_chars:
                    raise ValueError(
                        "chave JSON excede o limite de "
                        f"{max_string_chars} caracteres"
                    )
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def strict_json_loads(
    content: str | bytes,
    *,
    label: str = "JSON",
    max_depth: int = MAX_JSON_DEPTH,
    max_nodes: int = MAX_JSON_NODES,
    max_string_chars: int = MAX_JSON_STRING_CHARS,
) -> Any:
    """Decodifica UTF-8, rejeitando duplicatas, NaN e payloads patológicos."""
    try:
        text = content.decode("utf-8") if isinstance(content, bytes) else content
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
        _validate_limits(
            value,
            max_depth=max_depth,
            max_nodes=max_nodes,
            max_string_chars=max_string_chars,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as error:
        raise MiraiRuntimeError(f"{label} inválido: {error}") from error
    return value


def strict_json_dumps(
    value: Any,
    *,
    pretty: bool = False,
    sort_keys: bool = False,
) -> str:
    """Produz JSON RFC 8259: nunca serializa NaN ou Infinity."""
    options: dict[str, Any] = {
        "allow_nan": False,
        "ensure_ascii": False,
        "sort_keys": sort_keys,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    try:
        return json.dumps(value, **options)
    except (RecursionError, TypeError, ValueError) as error:
        raise MiraiRuntimeError(f"valor não pode ser serializado como JSON: {error}") from error


def canonical_json_bytes(value: Any) -> bytes:
    """Codificação determinística usada em hashes, recibos e auditoria."""
    return strict_json_dumps(value, sort_keys=True).encode("utf-8")
