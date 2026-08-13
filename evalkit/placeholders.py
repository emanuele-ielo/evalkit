"""Render the dynamic placeholders supported by Wonderful eval tool mocks.

The syntax intentionally mirrors the platform implementation so a scenario has
the same mock payload whether it is collected directly by EvalKit or executed
as a final Wonderful eval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class PlaceholderSyntaxError(ValueError):
    pass


class PlaceholderRenderError(RuntimeError):
    pass


_PLACEHOLDER_RE = re.compile(r'\{\{((?:[^\{\}"]*|"[^"]*")*)\}\}', re.DOTALL)
_KEYWORDS = frozenset(
    {
        "now",
        "today",
        "tomorrow",
        "yesterday",
        "start_of_today",
        "end_of_today",
        "start_of_tomorrow",
        "end_of_tomorrow",
    }
)
_UNIT_TO_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_TIME_HEAD_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*(.*)$", re.DOTALL)
_OFFSET_RE = re.compile(r"([+-])\s*(\d+)([A-Za-z])")
_NAMED_FORMATS = frozenset({"iso", "iso8601", "date", "time", "datetime", "unix", "unix_ms"})
_ARG_RE = re.compile(r"^(format|tz)\s*=\s*(.+)$", re.DOTALL)
_LITERAL_RE = re.compile(r'^"([^"]*)"$', re.DOTALL)


@dataclass(frozen=True)
class _Expression:
    kind: str
    keyword: str | None = None
    offsets: tuple[tuple[int, int], ...] = ()
    literal: str | None = None
    format: str | None = None
    tz: str | None = None


def _split_pipes(value: str) -> list[str]:
    parts: list[str] = []
    buffer: list[str] = []
    in_quote = False
    for character in value:
        if character == '"':
            in_quote = not in_quote
            buffer.append(character)
        elif character == "|" and not in_quote:
            parts.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(character)
    parts.append("".join(buffer).strip())
    return parts


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def parse(expression: str) -> _Expression:
    value = expression.strip()
    if not value:
        raise PlaceholderSyntaxError("empty placeholder expression")

    head, *pipes = _split_pipes(value)
    literal = _LITERAL_RE.match(head)
    if literal:
        return _Expression(kind="literal", literal=literal.group(1))

    match = _TIME_HEAD_RE.match(head)
    if not match:
        raise PlaceholderSyntaxError(f"could not parse placeholder: {expression!r}")
    keyword, remainder = match.group(1), match.group(2).strip()
    if keyword not in _KEYWORDS:
        raise PlaceholderSyntaxError(f"unknown placeholder keyword: {keyword!r}")

    offsets: list[tuple[int, int]] = []
    position = 0
    while position < len(remainder):
        while position < len(remainder) and remainder[position].isspace():
            position += 1
        if position >= len(remainder):
            break
        offset = _OFFSET_RE.match(remainder, position)
        if not offset:
            raise PlaceholderSyntaxError(
                f"could not parse duration offset in {expression!r} at {remainder[position:]!r}"
            )
        unit = offset.group(3)
        if unit not in _UNIT_TO_SECONDS:
            raise PlaceholderSyntaxError(f"unknown duration unit {unit!r} in {expression!r}")
        sign = 1 if offset.group(1) == "+" else -1
        offsets.append((sign, int(offset.group(2)) * _UNIT_TO_SECONDS[unit]))
        position = offset.end()
    if offsets and keyword != "now":
        raise PlaceholderSyntaxError(f"duration offsets are only allowed with 'now', got {keyword!r}")

    output_format: str | None = None
    timezone_name: str | None = None
    for argument in pipes:
        arg_match = _ARG_RE.match(argument)
        if not arg_match:
            raise PlaceholderSyntaxError(f"could not parse argument {argument!r} in {expression!r}")
        name, argument_value = arg_match.group(1), _strip_quotes(arg_match.group(2))
        if name == "format":
            if output_format is not None:
                raise PlaceholderSyntaxError(f"duplicate 'format=' argument in {expression!r}")
            output_format = argument_value
        else:
            if timezone_name is not None:
                raise PlaceholderSyntaxError(f"duplicate 'tz=' argument in {expression!r}")
            try:
                ZoneInfo(argument_value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise PlaceholderSyntaxError(f"unknown timezone {argument_value!r} in {expression!r}") from exc
            timezone_name = argument_value
    if output_format is not None and "%" not in output_format and output_format not in _NAMED_FORMATS:
        raise PlaceholderSyntaxError(f"unknown format {output_format!r} in {expression!r}")

    return _Expression(
        kind="time",
        keyword=keyword,
        offsets=tuple(offsets),
        format=output_format,
        tz=timezone_name,
    )


def render(value: Any, *, now: datetime) -> Any:
    if now.tzinfo is None:
        raise TypeError("render() requires a timezone-aware datetime")
    if isinstance(value, str):
        if "{{" not in value:
            return value
        return _PLACEHOLDER_RE.sub(lambda match: _evaluate(parse(match.group(1)), now), value)
    if isinstance(value, dict):
        return {key: render(item, now=now) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, now=now) for item in value]
    return value


def _evaluate(expression: _Expression, now: datetime) -> str:
    if expression.kind == "literal":
        return expression.literal or ""
    if expression.kind != "time" or expression.keyword is None:
        raise PlaceholderRenderError(f"unsupported expression kind: {expression.kind}")

    current = now.astimezone(ZoneInfo(expression.tz) if expression.tz else timezone.utc)
    midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = current.replace(hour=23, minute=59, second=59, microsecond=999_999)
    resolved = {
        "now": current,
        "today": midnight,
        "start_of_today": midnight,
        "end_of_today": end_of_day,
        "tomorrow": midnight + timedelta(days=1),
        "start_of_tomorrow": midnight + timedelta(days=1),
        "end_of_tomorrow": end_of_day + timedelta(days=1),
        "yesterday": midnight - timedelta(days=1),
    }[expression.keyword]
    for sign, seconds in expression.offsets:
        resolved += timedelta(seconds=sign * seconds)
    return _format_datetime(resolved, expression.format)


def _format_datetime(value: datetime, output_format: str | None) -> str:
    if output_format is None or output_format in {"iso", "iso8601"}:
        if value.utcoffset() is not None and value.utcoffset().total_seconds() == 0:
            return value.replace(tzinfo=None).isoformat() + "Z"
        return value.isoformat()
    if output_format == "date":
        return value.strftime("%Y-%m-%d")
    if output_format == "time":
        return value.strftime("%H:%M:%S")
    if output_format == "datetime":
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if output_format == "unix":
        return str(int(value.timestamp()))
    if output_format == "unix_ms":
        return str(int(value.timestamp() * 1000))
    if output_format and "%" in output_format:
        return value.strftime(output_format)
    raise PlaceholderRenderError(f"unhandled format: {output_format!r}")


__all__ = ["PlaceholderRenderError", "PlaceholderSyntaxError", "parse", "render"]
