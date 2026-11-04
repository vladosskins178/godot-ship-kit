"""Findings and how they are printed for humans, agents and CI."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, TextIO


class Severity(StrEnum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"

    @property
    def rank(self) -> int:
        return ["ok", "info", "warn", "error"].index(self.value)


@dataclass
class Finding:
    check: str
    severity: Severity
    title: str
    detail: str = ""
    fix: str = ""
    docs: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["severity"] = self.severity.value
        return {k: v for k, v in out.items() if v not in ("", {}, [])}


class Style:
    """ANSI styling that switches itself off when output is not a terminal."""

    def __init__(self, stream: TextIO | None = None, enabled: bool | None = None) -> None:
        stream = stream or sys.stdout
        if enabled is None:
            if os.environ.get("FORCE_COLOR"):
                enabled = True
            else:
                tty = hasattr(stream, "isatty") and stream.isatty()
                enabled = tty and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, t: str) -> str:
        return self._wrap("1", t)

    def dim(self, t: str) -> str:
        return self._wrap("2", t)

    def red(self, t: str) -> str:
        return self._wrap("31", t)

    def green(self, t: str) -> str:
        return self._wrap("32", t)

    def yellow(self, t: str) -> str:
        return self._wrap("33", t)

    def blue(self, t: str) -> str:
        return self._wrap("34", t)

    def cyan(self, t: str) -> str:
        return self._wrap("36", t)


_ICONS = {
    Severity.OK: ("ok", "green"),
    Severity.INFO: ("info", "blue"),
    Severity.WARN: ("warn", "yellow"),
    Severity.ERROR: ("FAIL", "red"),
}


def badge(sev: Severity, style: Style) -> str:
    label, color = _ICONS[sev]
    paint: Callable[[str], str] = getattr(style, color)
    return paint(f"{label:>4}")


def print_findings(
    title: str,
    findings: list[Finding],
    stream: TextIO | None = None,
    verbose: bool = False,
) -> None:
    stream = stream or sys.stdout
    style = Style(stream)
    stream.write(style.bold(title) + "\n\n")
    for f in findings:
        if f.severity == Severity.OK and not verbose:
            continue
        stream.write(f"  {badge(f.severity, style)}  {f.title}\n")
        if f.detail:
            for line in f.detail.splitlines():
                stream.write(f"        {style.dim(line)}\n")
        if f.fix and f.severity in (Severity.WARN, Severity.ERROR, Severity.INFO):
            first, *rest = f.fix.splitlines()
            stream.write(f"        {style.cyan('fix:')} {first}\n")
            for line in rest:
                stream.write(f"             {line}\n")
        if f.docs and f.severity != Severity.OK:
            stream.write(f"        {style.dim('docs: ' + f.docs)}\n")
    counts = summary_counts(findings)
    passed = counts["ok"]
    stream.write(
        "\n  "
        + style.green(f"{passed} passed")
        + "  "
        + style.red(f"{counts['error']} errors")
        + "  "
        + style.yellow(f"{counts['warn']} warnings")
        + "  "
        + style.blue(f"{counts['info']} notes")
        + "\n"
    )
    if not verbose and passed:
        stream.write(style.dim("  (run with --verbose to list passing checks)") + "\n")


def summary_counts(findings: list[Finding]) -> dict[str, int]:
    counts = {s.value: 0 for s in Severity}
    for f in findings:
        counts[f.severity.value] += 1
    return counts


def findings_markdown(title: str, findings: list[Finding]) -> str:
    lines = [f"# {title}", ""]
    order = [Severity.ERROR, Severity.WARN, Severity.INFO, Severity.OK]
    names = {
        Severity.ERROR: "Errors",
        Severity.WARN: "Warnings",
        Severity.INFO: "Notes",
        Severity.OK: "Passed",
    }
    for sev in order:
        group = [f for f in findings if f.severity == sev]
        if not group:
            continue
        lines += [f"## {names[sev]} ({len(group)})", ""]
        for f in group:
            lines.append(f"- **{f.title}** (`{f.check}`)")
            if f.detail:
                lines += [f"  {line}" for line in f.detail.splitlines()]
            if f.fix and sev != Severity.OK:
                lines.append(f"  - Fix: {f.fix}")
            if f.docs and sev != Severity.OK:
                lines.append(f"  - Docs: {f.docs}")
        lines.append("")
    return "\n".join(lines)


def dump_json(payload: Any, stream: TextIO | None = None) -> None:
    stream = stream or sys.stdout
    json.dump(payload, stream, indent=2, default=str)
    stream.write("\n")


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
