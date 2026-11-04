"""Reader and minimal editor for Godot's text config formats.

Covers ``project.godot``, ``export_presets.cfg``, ``*.import`` sidecars,
``export_credentials.cfg`` and the ``[resource]`` part of editor settings.
Values are parsed into plain Python types where that is unambiguous; anything
else (``Vector2(1, 2)``, ``SubResource("x")``) becomes a :class:`GodotCall`.

Editing is deliberately line-based so that a file round-trips byte for byte
apart from the keys that were changed. That matters for ``.import`` files,
which live in version control next to the assets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SECTION_RE = re.compile(r"^\[([^\]\s]+)([^\]]*)\]\s*$")
_KEY_RE = re.compile(r"^([A-Za-z0-9_/.\-:\"@]+)\s*=\s*(.*)$")


@dataclass(frozen=True)
class GodotCall:
    """A constructor-style value such as ``PackedStringArray("4.6")``."""

    name: str
    args: tuple[Any, ...]


@dataclass
class ConfigFile:
    """Parsed sections in file order. Keys keep their original spelling."""

    sections: dict[str, dict[str, Any]] = field(default_factory=dict)

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.sections.get(section, {}).get(key, default)

    def section(self, name: str) -> dict[str, Any]:
        return self.sections.get(name, {})

    def has_section(self, name: str) -> bool:
        return name in self.sections


class _Balance:
    """Tracks open brackets and strings across the lines of one value."""

    def __init__(self) -> None:
        self.depth = 0
        self.in_str = False
        self.escape = False

    def feed(self, text: str) -> bool:
        """Consume ``text``; return True when everything opened so far is closed."""
        for ch in text:
            if self.in_str:
                if self.escape:
                    self.escape = False
                elif ch == "\\":
                    self.escape = True
                elif ch == '"':
                    self.in_str = False
                continue
            if ch == '"':
                self.in_str = True
            elif ch in "([{":
                self.depth += 1
            elif ch in ")]}":
                self.depth -= 1
        return self.depth <= 0 and not self.in_str


def parse(text: str) -> ConfigFile:
    """Parse Godot config text. Unparseable values are kept as raw strings."""
    cfg = ConfigFile()
    current = ""
    cfg.sections[current] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        i += 1
        if not stripped or stripped.startswith(";"):
            continue
        sec = _SECTION_RE.match(stripped)
        if sec and "=" not in stripped.split("]")[0].split(" ")[0]:
            current = sec.group(1)
            cfg.sections.setdefault(current, {})
            continue
        kv = _KEY_RE.match(stripped)
        if not kv:
            continue
        key, raw = kv.group(1), kv.group(2)
        balance = _Balance()
        if not balance.feed(raw):
            parts = [raw]
            while i < len(lines):
                parts.append(lines[i])
                i += 1
                if balance.feed("\n" + lines[i - 1]):
                    break
            raw = "\n".join(parts)
        cfg.sections[current][key] = parse_value(raw)
    if not cfg.sections[""]:
        del cfg.sections[""]
    return cfg


def load(path: str | Path) -> ConfigFile:
    return parse(Path(path).read_text(encoding="utf-8", errors="replace"))


# --------------------------------------------------------------------------
# Value parsing
# --------------------------------------------------------------------------


class _Parser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.i = 0

    def ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
            self.i += 1

    def peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def value(self) -> Any:
        self.ws()
        ch = self.peek()
        if ch == '"':
            return self.string()
        if ch == "&" and self.s[self.i + 1 : self.i + 2] == '"':  # StringName
            self.i += 1
            return self.string()
        if ch == "[":
            return self.array()
        if ch == "{":
            return self.dict()
        if ch == "-" or ch.isdigit():
            return self.number()
        return self.word()

    def string(self) -> str:
        assert self.peek() == '"'
        self.i += 1
        out: list[str] = []
        while self.i < len(self.s):
            ch = self.s[self.i]
            self.i += 1
            if ch == "\\" and self.i < len(self.s):
                nxt = self.s[self.i]
                self.i += 1
                out.append({"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(nxt, "\\" + nxt))
            elif ch == '"':
                return "".join(out)
            else:
                out.append(ch)
        raise ValueError("unterminated string")

    def array(self) -> list[Any]:
        self.i += 1
        items: list[Any] = []
        while True:
            self.ws()
            if self.peek() == "]":
                self.i += 1
                return items
            items.append(self.value())
            self.ws()
            if self.peek() == ",":
                self.i += 1
            elif self.peek() != "]":
                raise ValueError("bad array")

    def dict(self) -> dict[Any, Any]:
        self.i += 1
        out: dict[Any, Any] = {}
        while True:
            self.ws()
            if self.peek() == "}":
                self.i += 1
                return out
            key = self.value()
            self.ws()
            if self.peek() != ":":
                raise ValueError("bad dict")
            self.i += 1
            out[key if isinstance(key, (str, int, float)) else repr(key)] = self.value()
            self.ws()
            if self.peek() == ",":
                self.i += 1

    def number(self) -> int | float:
        m = re.compile(r"-?(?:\d+\.?\d*(?:e[-+]?\d+)?|inf|nan)", re.I).match(self.s, self.i)
        if not m:
            raise ValueError("bad number")
        self.i = m.end()
        tok = m.group(0)
        try:
            return int(tok)
        except ValueError:
            return float(tok)

    def word(self) -> Any:
        m = re.compile(r"[A-Za-z_][A-Za-z0-9_]*").match(self.s, self.i)
        if not m:
            raise ValueError("unexpected token")
        self.i = m.end()
        name = m.group(0)
        if name == "true":
            return True
        if name == "false":
            return False
        if name == "null":
            return None
        if name in ("inf", "nan"):
            return float(name)
        self.ws()
        if self.peek() != "(":
            return GodotCall(name, ())
        self.i += 1
        args: list[Any] = []
        while True:
            self.ws()
            if self.peek() == ")":
                self.i += 1
                return GodotCall(name, tuple(args))
            args.append(self.value())
            self.ws()
            if self.peek() == ",":
                self.i += 1


def parse_value(raw: str) -> Any:
    raw = raw.strip()
    try:
        p = _Parser(raw)
        val = p.value()
        p.ws()
        if p.i != len(raw):
            return raw
        return val
    except (ValueError, AssertionError, IndexError):
        return raw


def as_list(value: Any) -> list[Any]:
    """Flatten ``PackedStringArray(...)`` / ``[...]`` into a list."""
    if isinstance(value, GodotCall):
        return list(value.args)
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def format_value(value: Any) -> str:
    """Serialise a simple Python value the way Godot writes it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        text = repr(value)
        return text if "." in text or "e" in text else text + ".0"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    raise TypeError(f"cannot format {type(value).__name__}")


# --------------------------------------------------------------------------
# Line-based editing
# --------------------------------------------------------------------------


def set_values(text: str, section: str, values: dict[str, Any]) -> str:
    """Return ``text`` with ``values`` written into ``[section]``.

    Existing single-line keys are replaced in place; missing keys are appended
    after the last key of the section. The section is created if absent.
    """
    lines = text.splitlines(keepends=True)
    newline = "\r\n" if text.endswith("\r\n") else "\n"
    pending = dict(values)
    start = end = None
    for idx, line in enumerate(lines):
        m = _SECTION_RE.match(line.strip())
        if m:
            if start is not None and end is None:
                end = idx
            if m.group(1) == section:
                start = idx
    if start is not None and end is None:
        end = len(lines)

    if start is None:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        if lines and lines[-1].strip():
            lines.append(newline)
        lines.append(f"[{section}]{newline}{newline}")
        for key, val in pending.items():
            lines.append(f"{key}={format_value(val)}{newline}")
        return "".join(lines)

    assert end is not None
    last_key_line = start
    for idx in range(start + 1, end):
        kv = _KEY_RE.match(lines[idx].strip())
        if not kv:
            continue
        last_key_line = idx
        key = kv.group(1)
        if key in pending:
            eol = newline if lines[idx].endswith(("\n", "\r")) else ""
            lines[idx] = f"{key}={format_value(pending.pop(key))}{eol}"
    if pending:
        insert = [f"{k}={format_value(v)}{newline}" for k, v in pending.items()]
        if not lines[last_key_line].endswith(("\n", "\r")):
            lines[last_key_line] += newline
        lines[last_key_line + 1 : last_key_line + 1] = insert
    return "".join(lines)
