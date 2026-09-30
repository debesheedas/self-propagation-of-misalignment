"""THE STORE — where a cell lives, how a frozen payload is read, which pairs the provider refused.

    runs/<Q>/<SCN>/<model>/<arm>.json              a cell, filed under the experiment that owns its arm (`arm_owner`)
    runs/controls/<SCN>/<model>/<arm>.json         the shared controls, stored once
    runs/forge/<Q>/<SCN>/<model>.json              the search record a payload came from (not shipped)
    runs/payloads/winners/<SCN>/<model>__<Q>.json  the frozen payload a cell replays
    runs/scn/<SCN>/derived/<name>.json             per-scenario derived files
    runs/_index/refusal_mask.json                  the (scenario, model) pairs the provider refused on every attempt

`SUITE_ROOT` in the environment redirects the whole store. Absent is never zero: a missing cell is None.
"""
from __future__ import annotations
import glob
import json
import os
import sys as _sys
from pathlib import Path as _P

from .. import records as R
from ..redteam.rq_spec import _BASE as _ARM_BASES

SUITE_ROOT = os.environ.get("SUITE_ROOT", "runs")

# The arm BASES, from the single vocabulary in `core.redteam.rq_spec`.
ARMS = tuple(_ARM_BASES)


def split_stem(stem: str):
    """`reps_<model>_<arm>` -> (model, arm). Split on the ARM, never on the last underscore: `benign_note`
    contains one, so `rpartition("_")` silently produced model="deepseek-v4_benign", arm="note" — which split one
    real cell into two phantom ones and made the benign-note control unreadable in the report."""
    for a in sorted(ARMS, key=len, reverse=True):
        if stem.endswith("_" + a):
            return stem[: -len(a) - 1], a
    model, _, arm = stem.rpartition("_")
    return model, arm


REFUSAL_ERROR = "provider_refusal: "


def rep_provider_refused(rep: dict) -> bool:
    """Did a provider safety guard cut ANY generation of this rep? Such a rep is invalid — excluded from the
    numerator and the denominator of every rate — because the model never ran the session we meant to sample."""
    for g in (rep.get("gens") or []):
        if (g or {}).get("stop") == "refusal":
            return True
    # A rep already stamped by the runner (or by the re-score pass) stays refused even if `gens` was trimmed.
    return str(rep.get("error") or "").startswith(REFUSAL_ERROR)


# ---- THE REFUSAL MASK, as the runners see it. A masked pair is skipped before the first API call and rendered
# REFUSED rather than as a rate. `SP_NO_REFUSAL_MASK=1` bypasses it, for re-testing a pair after a world edit.
_MASK_CACHE: dict | None = None


def _mask_data() -> dict:
    global _MASK_CACHE
    if _MASK_CACHE is None:
        path = os.path.join(SUITE_ROOT, "_index", "refusal_mask.json")
        try:
            with open(path) as fh:
                _MASK_CACHE = json.load(fh)
        except FileNotFoundError:
            _MASK_CACHE = {}                                     # no mask yet is a legitimate state
        except Exception as e:                                   # noqa: BLE001
            # LOUD. An unreadable mask must not silently unmask the whole suite -- the first version of this
            # swallowed a NameError (json was not imported) and every pair came back unmasked, which is the
            # exact shape of failure the mask exists to prevent.
            print(f"[mask] WARNING: could not read {path} ({type(e).__name__}: {e}) — "
                  f"NOTHING will be masked this run", file=_sys.stderr)
            _MASK_CACHE = {}
    return _MASK_CACHE


def is_masked(scn: str, model: str, rq: str = "") -> tuple[bool, str]:
    """-> (skip?, why). A masked pair must be SKIPPED BEFORE the first API call, not measured and discarded.

    ONE SCENARIO LIST PER MODEL, EVERY QUESTION (2026-08-21; multi-model 2026-08-22). For a declared
    model the answer depends on the SCENARIO alone: if the row is in that model's list the pair is masked for
    every research question and for the controls alike. `rq` is accepted for call-site compatibility and
    deliberately ignored for those models. Two models are declared today — `fable-5` (15 rows, the provider
    guard refuses gen-0) and `gemini-3.6-flash` (H05, Google's content filter blocks ~2 of 3 attempts).

    WHAT THIS REPLACED AND WHY. The previous artifact carried a per-question map (`hard_by_rq`): Q1/Q6 and
    controls masked the ten H rows, the Q2 family also masked U01-U03, and controls took the intersection so
    that a shared control cell was never denied to a question that still wanted it. That was precise and it
    was genuinely hard to reason about — a reader had to know which list a given table had been rendered
    under, and the carve-out meant sweeps kept paying for control cells on rows every attack question had
    already given up on. One list is worth more than the precision it costs.

    The distinction the caller must preserve is unchanged: a masked cell is NOT `0/n`. It is "not measured,
    the provider refuses here". Writing it as a rate is the `index_table misses all_refused` bug, where a cell
    renders a clean zero while every rep was refused.
    """
    if os.environ.get("SP_NO_REFUSAL_MASK"):
        return False, ""
    d = _mask_data()
    if not d:
        return False, ""
    try:                                                          # live registry only
        import sys as _sys
        if "scripts" not in _sys.path:
            _sys.path.insert(0, "scripts")
        from _suite import _suite_rows
        if scn not in _suite_rows():
            return False, ""
    except Exception:                                             # noqa: BLE001
        return False, ""
    # PER-MODEL ROW LISTS. Was a single `declared_model` + `declared_rows` pair while fable-5 was the only
    # blocked model; a second one (gemini-3.6-flash x H05, Google's content filter) arrived 2026-08-22, so the
    # artifact now carries `declared: {model: [rows]}`. The semantics per entry are unchanged.
    # A CROSS-MODEL QUESTION IS NOT COVERED BY A DECLARATION ABOUT GEN-0 (2026-08-24).
    #
    # Every declared entry exists because the provider guard refuses that model AS THE AUTHOR — `declared_note`
    # and each manual entry say so, and the refusal is measured on gen-0. On a cross-model arm the masked model
    # is the EXECUTOR and some other model runs gen-0, so the premise the declaration rests on is simply absent
    # and the mask's own tooltip ("no cell is ever written") is false.
    #
    # MEASURED, which is why this is a carve-out and not an opinion: U06::fable-5 is on the declared list, and
    # its stored cross-model cells are `instruction-xauthor` 3/6 fired / 4/6 banked and `neutral_chain-xauthor`
    # 0/8 — 14 of 16 sessions ended on `finish`, both cells stamped `trustworthy: ok`, with opus-5 authoring.
    # The derived half of the artifact agrees: `soft_not_masked` lists `U06::fable-5` explicitly. The mask was
    # hiding a real cross-model break behind the word REFUSED.
    #
    # DELIBERATELY SCOPED TO THE QUESTION, NOT THE MODEL. Same-model arms for a declared pair stay masked, which
    # is every other table; only a question whose artifact carries the `-xauthor` suffix is exempt, and only
    # because on those arms this model never holds the author's chair.
    try:
        from ..redteam.rq_spec import BY_ID as _BY_ID
        _q = _BY_ID.get((rq or "").upper())
        if _q is not None and getattr(_q, "artifact", "").endswith("-xauthor"):
            return False, ""
    except Exception:                                             # noqa: BLE001
        pass
    _rows = (d.get("declared") or {}).get(model) or []
    if scn in _rows:
        return True, (f"{scn}::{model} is masked: {scn} is one of the {len(_rows)} scenario(s) declared "
                      f"unrunnable for {model} across every question and the controls (refusal_mask.json)")
    # The derived/manual flat list still applies to any OTHER model that starts refusing. It is not reachable
    # for the declared model above, whose rows are a superset of its manual entries.
    pair = f"{scn}::{model}"
    if pair in (d.get("hard") or []):
        why = (d.get("hard_manual") or {}).get(pair)
        return True, f"{pair} is masked: {str(why)[:160] if why else 'provider refuses (refusal_mask.json)'}"
    return False, ""


# ---- THE PATH API
def arm_owner(arm: str) -> str:
    """The folder under `runs/` that owns a cell with this arm. ONE definition, shared by the readers and by
    `scripts/migrate_store_layout.py`, so a file can never be written somewhere the reader will not look.

    Each question declares exactly one arm, so the exact mapping is 1:1. Two extensions the registry does not
    state but the data needs: a SUFFIXED VARIANT follows its exact-arm owner (`disposition-memstrong` goes where
    `disposition-screen1` goes — same experiment, one dial moved), and `note` is Q1's un-forged arm (the arm
    vocabulary in `_suite_run` reads "note / note-forged … Q1"). Plain `disposition` has NO owning question — Q3
    now owns plain `disposition` — so an UNOWNED arm lands in `other-arms/` rather than being attributed to one that
    did not measure it. Controls belong to every question, so they are stored once under `controls/`.
    """
    from ..redteam.rq_spec import RQS, CONTROL_ARMS
    exact = {q.arm: q.id for q in RQS if q.arm != "__none__"}
    # A question may own arms beyond its primary one (`owns_arms`) — Q9's relay depths, Q11's closed and absent
    # arms — and can only name one of them in `arm`. Declared owners win over the stem walk below, which would
    # otherwise file `instruction-relay2` under Q2 because its stem is Q2's arm.
    for _q in RQS:
        for _a in (getattr(_q, "owns_arms", ()) or ()):
            exact.setdefault(_a, _q.id)
    base_owner = {"note": "Q1"}
    if arm in exact:
        return exact[arm]
    if arm in CONTROL_ARMS:
        return "controls"
    if arm in base_owner:
        return base_owner[arm]
    parts = arm.split("-")
    for i in range(len(parts) - 1, 0, -1):
        stem = "-".join(parts[:i])
        if stem in exact:
            return exact[stem]
        if stem in base_owner:
            return base_owner[stem]
    return "other-arms"


def scn_root(scn, root: str = SUITE_ROOT) -> _P:
    """Per-scenario root for DERIVED files. `read_derived` appends `/derived/`."""
    return _P(root) / "scn" / scn


def trace_path(scn, model, arm, root: str = SUITE_ROOT) -> _P:
    return _P(root) / arm_owner(arm) / scn / model / f"{arm}.json"


def redteam_path(scn, model, rq, root: str = SUITE_ROOT) -> _P:
    """The search record a frozen payload came from. Not shipped; read by the trace page when present."""
    return _P(root) / "forge" / rq / scn / f"{model}.json"


def winner_key(scn: str, model: str, rq: str) -> str:
    return f"{scn}::{model}::{rq}"


def winner_path(scn: str, model: str, rq: str, root: str = SUITE_ROOT) -> _P:
    """One file per winner, grouped by scenario: payloads/winners/<SCN>/<model>__<RQ>.json

    The third component is whatever the KEY's third component is. It is usually a question id (Q1…Q10), but
    pre-2026-07-27 records were keyed by ARM (`U10::gemini-3.1-pro::disposition`) and carry no `rq` field at all,
    so the path is derived from the key rather than from the entry's fields."""
    return _P(root) / "payloads" / "winners" / scn / f"{model}__{rq}.json"


def _key_parts(key: str):
    p = key.split("::")
    return (p + ["", "", ""])[:3]


def read_winners(root: str = SUITE_ROOT, rq: str = "", scn: str = "") -> dict:
    """Every saved forge-winning payload, keyed `<SCN>::<model>::<RQ>`.

    ONE FILE PER WINNER at payloads/winners/<SCN>/<model>__<RQ>.json. Sharded rather than one shared file because
    a shared file is a lost-update race the moment two searches run at once, and running two at once is the normal
    way to work. Every writer owns its own path, so no lock is needed.
    """
    out = {}
    for f in sorted(glob.glob(f"{root}/payloads/winners/*/*.json")):
        d = R.read_cell(f) or {}
        if not isinstance(d, dict):
            continue
        p = _P(f)
        model, _, third = p.stem.partition("__")
        out[winner_key(p.parent.name, model, third)] = d
    if rq:
        out = {k: v for k, v in out.items() if _key_parts(k)[2] == rq}
    if scn:
        out = {k: v for k, v in out.items() if _key_parts(k)[0] == scn}
    return out


def iter_traces(root: str = SUITE_ROOT):
    """(scn, model, arm, path) over runs/<owner>/<SCN>/<model>/<arm>.json; the forge/scn/env/payloads trees are skipped."""
    for f in sorted(glob.glob(f"{root}/*/[A-Z][0-9]*/*/*.json")):
        parts = _P(f).parts
        if parts[-4] in ("forge", "scn", "env", "payloads"):
            continue
        yield parts[-3], parts[-2], _P(f).stem, _P(f)

