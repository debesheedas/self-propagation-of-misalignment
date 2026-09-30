"""Agent / tool loop — the session runner the experiments share.

Presents a task to a model, exposes FAKE/log-only tools, runs a
multi-turn tool-use loop, and records every step via the sandbox stack.
"""

from .loop import SessionResult, run_session
from .tools import Tool, ToolCall

__all__ = [
    "run_session",
    "SessionResult",
    "Tool",
    "ToolCall",
]
