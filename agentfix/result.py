"""Canonical operation results shared by CLI and integration adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from agentfix import report as rep


class StatusText(str):
    """String-compatible adapter text carrying an explicit operation status."""

    status: str

    def __new__(cls, value: str, status: str = "ok") -> "StatusText":
        instance = str.__new__(cls, value)
        instance.status = status
        return instance


class StatusLines(list):
    """List-compatible adapter output carrying an explicit operation status."""

    status: str

    def __init__(self, values: List[object] = (), status: str = "ok") -> None:
        super().__init__(values)
        self.status = status


def status_of(value: object, default: str = "ok") -> str:
    """Read an explicit adapter status without interpreting human text."""
    return str(getattr(value, "status", default))


def aggregate_status(values: List[object], default: str = "ok") -> str:
    """Aggregate statuses from typed line adapters in severity order."""
    statuses = [status_of(value, default) for value in values]
    if "error" in statuses:
        return "error"
    if "inconclusive" in statuses:
        return "inconclusive"
    return default


@dataclass
class OperationResult:
    operation: str
    status: str
    lines: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def error(self) -> bool:
        return self.status == "error"

    def text(self) -> str:
        return "\n".join(self.lines)


def from_lines(operation: str, lines: List[str], status: str) -> OperationResult:
    """Normalize legacy line adapters with an explicit status supplied by the owner."""
    if status not in {"ok", "error", "inconclusive"}:
        raise ValueError(f"unsupported operation status: {status}")
    normalized = [rep.mask_secrets(str(line)) for line in lines]
    return OperationResult(operation=operation, status=status, lines=normalized)
