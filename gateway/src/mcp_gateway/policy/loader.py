"""Load ``policies.yaml`` and validate it, failing closed.

Any problem (unreadable file, YAML syntax, duplicate keys, schema or cross-reference
violations) raises :class:`PolicyLoadError` listing the problems found, each prefixed with
its location in the document. Validation runs in stages; within a stage every problem is
reported at once. Cross-reference checks run once the document's shape is valid.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Hashable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from yaml.constructor import ConstructorError
from yaml.error import Mark
from yaml.nodes import MappingNode

from .models import (
    MODEL_ID_REGEX,
    POLICY_NAME_REGEX,
    QUALIFIED_TOOL_NAME_REGEX,
    ROLE_NAME_REGEX,
    TOOL_PATTERN_REGEX,
    PolicyDocument,
)

_YAML_MERGE_TAG = "tag:yaml.org,2002:merge"

_PATTERN_HINTS = {
    QUALIFIED_TOOL_NAME_REGEX: (
        "must be '<server>__<tool>': server = lowercase letters/digits separated by single "
        "hyphens, tool = letters, digits, '_' or '-' (64 characters at most)"
    ),
    TOOL_PATTERN_REGEX: "glob patterns may use letters, digits, '_', '-', '*' and '?' only",
    ROLE_NAME_REGEX: "role names are lowercase, start with a letter, max 32 characters",
    MODEL_ID_REGEX: "not a valid model id",
    POLICY_NAME_REGEX: "lowercase letters, digits, '.', '_' or '-' (64 characters at most)",
}


class PolicyLoadError(Exception):
    """The policy cannot be used. The gateway must not start (or reload) with it."""

    def __init__(self, source: str, problems: Sequence[str]) -> None:
        self.source = source
        self.problems = tuple(problems)
        super().__init__(
            "\n".join([f"invalid policy {source}:", *(f"  - {p}" for p in self.problems)])
        )


@dataclass(frozen=True, slots=True)
class LoadedPolicy:
    """A validated policy plus provenance recorded with every decision it makes."""

    document: PolicyDocument
    digest: str
    """``sha256:<hex>`` of the exact bytes loaded; identifies the policy revision."""
    source: str


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """``yaml.SafeLoader`` that rejects duplicate mapping keys.

    Plain PyYAML silently keeps the *last* duplicate, so a second ``update_credit_limit``
    entry without ``requires_approval`` would quietly remove an approval gate. Keys brought
    in through YAML merge keys (``<<``) may still be overridden explicitly, as YAML intends.
    """

    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        first_seen: dict[Hashable, Mark] = {}
        for key_node, _value_node in node.value:
            if key_node.tag == _YAML_MERGE_TAG:
                continue
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, Hashable):
                continue  # the base constructor reports unhashable keys
            if key in first_seen:
                first = first_seen[key]
                raise ConstructorError(
                    f"first defined at line {first.line + 1}",
                    first,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            first_seen[key] = key_node.start_mark
        return super().construct_mapping(node, deep=deep)


def parse_policy(content: str | bytes, *, source: str = "<memory>") -> LoadedPolicy:
    """Parse and validate policy YAML from memory."""
    raw = content.encode() if isinstance(content, str) else content
    digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    try:
        data = yaml.load(raw, Loader=_UniqueKeySafeLoader)  # noqa: S506 - SafeLoader subclass
    except yaml.YAMLError as exc:
        raise PolicyLoadError(source, [_format_yaml_error(exc)]) from exc
    if not isinstance(data, dict):
        raise PolicyLoadError(source, ["the document must be a YAML mapping"])
    if problems := list(_non_json_values(data)):
        raise PolicyLoadError(source, problems)
    try:
        document = PolicyDocument.from_data(data)
    except ValidationError as exc:
        raise PolicyLoadError(source, format_validation_errors(exc)) from exc
    return LoadedPolicy(document=document, digest=digest, source=source)


def load_policy(path: str | Path) -> LoadedPolicy:
    """Read, parse and validate a policy file."""
    policy_path = Path(path)
    try:
        raw = policy_path.read_bytes()
    except OSError as exc:
        raise PolicyLoadError(str(policy_path), [f"cannot read file: {exc.strerror}"]) from exc
    return parse_policy(raw, source=str(policy_path))


def format_validation_errors(exc: ValidationError) -> list[str]:
    """Render Pydantic errors as ``dotted.path: message`` lines."""
    problems: list[str] = []
    for error in exc.errors(include_url=False, include_input=False):
        location = _format_location(error["loc"])
        if error["type"] == "extra_forbidden":
            message = "unknown field (closed schema; check the spelling)"
        elif error["type"] == "string_pattern_mismatch":
            pattern = (error.get("ctx") or {}).get("pattern", "")
            message = _PATTERN_HINTS.get(pattern, error["msg"])
        else:
            message = error["msg"].removeprefix("Value error, ")
        for line in message.splitlines():
            # Cross-reference messages already start with their own location.
            problems.append(f"{location}: {line}" if location else line)
    return problems


def _format_location(loc: tuple[int | str, ...]) -> str:
    path = ""
    for part in loc:
        if part == "[key]":
            path += " (key)"
        elif isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else part
    return path


def _format_yaml_error(exc: yaml.YAMLError) -> str:
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
        mark = exc.problem_mark
        context = f" ({exc.context})" if exc.context else ""
        return f"YAML line {mark.line + 1}, column {mark.column + 1}: {exc.problem}{context}"
    return f"YAML: {exc}"


def _non_json_values(value: object, path: str = "") -> Iterator[str]:
    """Locate parsed YAML values that have no JSON equivalent (YAML timestamps, NaN,
    non-string keys such as an unquoted ``yes:``), so they are reported with a path
    instead of being silently converted."""
    here = path or "<root>"
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if not isinstance(key, str):
                yield f"{child}: mapping keys must be strings (YAML read a {type(key).__name__})"
            yield from _non_json_values(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _non_json_values(item, f"{path}[{index}]")
    elif isinstance(value, float) and not math.isfinite(value):
        yield f"{here}: {value} is not a finite number"
    elif value is not None and not isinstance(value, str | int | float):
        yield f"{here}: YAML read a {type(value).__name__}; quote the value to make it a string"
