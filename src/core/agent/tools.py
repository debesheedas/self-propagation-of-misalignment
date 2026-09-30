"""Tool primitives shared by the live sandbox stack.

Containment (plan §10): every tool is simulated — it records the call and returns
a plausible fake result, and performs **no real side effect**.

This module holds only the data primitives (``Tool``, ``ToolCall``) and the tiny
JSON-schema helpers (``_obj``, ``_STR``) used across ``harness/sandbox.py``,
``harness/capability.py`` and the experiments. The old "channel stack" harm-tool
library + deterministic ``committed_harm`` detection was retired to
``legacy/channel_stack/harm_tools.py`` (2026-07-08; see legacy/docs/REFACTOR_PLAN.md).
legacy/ itself was deleted 2026-08-03 — both live on in git history.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    result: str = ""
    error: str | None = None


@dataclass
class Tool:
    """A fake/log-only tool. ``parameters`` is a JSON-schema object; ``fn`` returns
    a simulated result string and must never cause a real side effect."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., str]
    harmful: bool = False  # for transparency in tracing/reports
    # A FUTURE capability this tool embodies (`gate_kind="cap"`): the tool is withheld from the session's toolset
    # entirely unless the named capability is in Sandbox.caps — the agent cannot call it, cannot see it, and has no
    # way to express the action. That is what distinguishes a CAPABILITY gate (the primitive does not exist yet)
    # from a PERMISSION gate (it exists, this session is not allowed to use it, and calling it raises
    # PermissionError). Filtered in harness.sandbox.build_sandbox_tools. Empty = always present.
    requires_cap: str = ""

    def run(self, args: dict[str, Any]) -> str:
        return self.fn(**args)


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or list(props)}


_STR = {"type": "string"}
