"""Stateful agentic sandbox for the deferred-capability study (the design notes).

A persistent, stateful sandbox simulating an autonomous agent on a shared multi-node cluster: a fake
filesystem, a per-session permission system (egress gating), an audit log, and a persistent memory —
all carried across multiple multi-turn sessions of one lineage. All tools are FAKE/log-only
(containment §10): no real egress, decryption, exploitation, or side effects.
"""

from .sandbox import Lineage, Sandbox, build_sandbox_tools

__all__ = ["Sandbox", "Lineage", "build_sandbox_tools"]
