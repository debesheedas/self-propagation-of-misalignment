"""Atomic JSON I/O and provenance stamps for the trace store.

A cell (`runs/<experiment>/<scenario>/<model>/<arm>.json`) holds every replay in full; per-scenario derived
files live under `runs/scn/<scenario>/derived/`. Paths are resolved by `core.report.suite_store`.
"""
from __future__ import annotations
import datetime
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


_GIT_SHA = None
def code_sha() -> str:
    """Provenance stamp for the harness at write time (cached); 'nogit' if unavailable: the short HEAD sha,
    plus a hash of the working-tree delta when the tree is dirty."""
    global _GIT_SHA
    if _GIT_SHA is None:
        try:
            head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                           stderr=subprocess.DEVNULL).decode().strip() or "nogit"
            status = subprocess.check_output(["git", "status", "--porcelain"],
                                             stderr=subprocess.DEVNULL).decode()
            if status.strip():
                diff = subprocess.check_output(["git", "diff", "HEAD"], stderr=subprocess.DEVNULL).decode()
                head += "+" + hashlib.sha256((status + diff).encode()).hexdigest()[:8]
            _GIT_SHA = head
        except Exception:
            _GIT_SHA = "nogit"
    return _GIT_SHA


def _atomic_write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


def read_cell(path) -> dict | None:
    p = Path(path)
    if not p.exists(): return None
    try: return json.loads(p.read_text())
    except Exception: return None


def rep_eval_aware_overruled(rep: dict):
    """A human override of this rep's eval-aware verdict, or None. The override records who made it, why,
    the flagged quote and a re-judge tally; the judge's own verdict is never erased."""
    o = (rep or {}).get("eval_aware_override")
    return o if isinstance(o, dict) and o.get("by") else None


def mem_prompt_version() -> str:
    """The memory-prompt convention every session was run under. Stamped onto every derived artifact because a
    change here is exactly what invalidated 30 vetted breaks on 2026-07-25 (10 of them did not survive the re-run) —
    a result that does not carry this stamp cannot be compared against one that does."""
    try:
        from .harness.sandbox import MEM_PROMPT_VERSION
        return MEM_PROMPT_VERSION
    except Exception:
        return "unknown"


def write_derived(run_dir, name, obj) -> Path:
    p = Path(run_dir) / "derived" / f"{name}.json"
    _atomic_write(p, {**obj, "written_at": _now(), "code_sha": code_sha(),
                      "mem_prompt_version": mem_prompt_version()}); return p


def read_derived(run_dir, name):
    """Read a derived file (calibration / validity verdict), or None. Carries the written_at/code_sha stamps."""
    return read_cell(Path(run_dir) / "derived" / f"{name}.json")

