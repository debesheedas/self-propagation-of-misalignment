#!/usr/bin/env python3
"""BUILD `traces.html` — one self-contained page holding one transcript per (experiment, scenario, model) cell.

    SUITE_ROOT=/path/to/runs uv run python scripts/build_traces.py --out traces.html
    ... --manifest runs/_index/paper_cells.json      # the shipped page: the paper's exact cell set for Q1-Q7

WHAT IS ON THE PAGE. For every experiment reported in the paper, every (scenario, model) cell that propagated,
and for each such cell ONE representative transcript: the replay in which the successor acted (or, for the
gate-shut experiment, attempted the action; for the memory-removed experiment, the replay in which the aim was
written to a file and then acted on). A cell that never propagated has no transcript here, exactly as it has
no bar in the figures. The intervening-sessions experiment contributes, per (scenario, model) chain, the
successor probe at depth 0, half-way and 100, one carrier session that engaged the inherited aim, and the whole
100-session lineage.

HOW IT STAYS ONE FILE. Every transcript is rendered once (`core.report.demo_trace`, the same renderer every
other view of a trace in this project uses), gzip-compressed, base64-encoded and parked in an inert
`<script type="text/plain">` block. A click inflates it in the browser with the native `DecompressionStream`
API (any 2023+ browser) and inserts it into the page. Nothing is fetched; the file works from disk.

READS the trace store only (`SUITE_ROOT`, default `runs/`) plus the scenario registry, so a store produced by
re-running the drivers renders the same way.
"""
from __future__ import annotations

import argparse
import base64
import glob
import gzip
import html
import json
import pathlib
import sys

sys.path[:0] = ["src", "scripts"]

import yaml                                                        # noqa: E402
from core import records as R                                      # noqa: E402
from core.redteam import rq_spec as RQ                             # noqa: E402
from core.report import demo_trace as D                            # noqa: E402
from core.report import suite_store as ST                          # noqa: E402
from core.report.scenario_names import (EXPERIMENTS, FAMILY, PAGE_ORDER, display, misalignment_class,  # noqa: E402
                                        model_name)
from _suite import ENVS, SUITE, load, scenario_fingerprint         # noqa: E402

ROOT = ST.SUITE_ROOT
PANEL = list(yaml.safe_load(open("configs/panel.yaml"))["panel"])

# The arms each experiment's transcripts come from, with the condition label a reader sees beside the model.
ARMS = {
    "Q1":  {"note-forged": ""},
    "Q2":  {"instruction": ""},
    "Q3":  {"disposition": ""},
    "Q5":  {"instruction-stealth": ""},
    "Q6":  {"note-defended": ""},
    "Q7":  {"instruction-defended": ""},
    "Q8":  {"instruction-gateshut": "gate SHUT — the permission is not granted",
            "instruction-nooccasion": "occasion ABSENT — the trigger never arrives",
            "instruction-tasksucc": "occasion PRESENT — the reference session"},
    "Q10": {"instruction-xauthor": "cross-model — a different model wrote the note",
            "instruction-xauthor-h2": "framing ladder h2 — the note as the persona's own values",
            "instruction-xauthor-h1": "framing ladder h1 — a bare transcription of the goal",
            "instruction-xauthor-h3": "framing ladder h3 — hidden among genuine checklist items",
            "instruction-xauthor-h4": "framing ladder h4 — marked as already checked and decided",
            "instruction-xauthor-h4b": "framing ladder h4 — independent replicate"},
    "Q11": {"instruction-memopen": "memory OPEN — the same world with the channel available",
            "instruction-memclosed": "memory CLOSED — every write to memory errors",
            "instruction-memabsent": "memory ABSENT — no memory tool, no mention of memory"},
    "Q9":  {},                                                     # decay chains, collected separately
}

_PANEL = set(PANEL)


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def _kn(s):
    try:
        a, b = str(s).split("/")
        return int(a), int(b)
    except Exception:
        return None


# ------------------------------------------------------------------------------------------------------------- #
# the per-scenario brief and the "why this is genuine" analysis
# ------------------------------------------------------------------------------------------------------------- #
def _brief(scn):
    return R.read_derived(str(ST.scn_root(scn)), "trace_brief") or {}


def _controls(scn, model):
    sm = R.read_derived(str(ST.scn_root(scn)), "summary") or {"cells": []}
    def cell(arm):
        c = next((x for x in sm["cells"] if x["model"] == model and x["arm"] == arm), None)
        return c["fired"] if c else "—"
    return cell("baseline"), cell("benign_note"), cell("neutral_chain")


def _analysis(scn, b, model, roll, q, medium=""):
    """The 'why this is a genuine failure' bullets. `medium` is 'closed'/'absent' on the memory-removed arms,
    whose propagation vector is a file rather than the memory note."""
    base, benign, neutral = _controls(scn, model)
    au = next((g for g in (roll.get("gens") or []) if g.get("role") == "author"), {})
    li = [f"<li><b>Empty-memory control {esc(base)}</b> — the same model in the same scenario with nothing in "
          f"memory does not do this on its own.</li>",
          f"<li><b>Benign-note control {esc(benign)}</b> — a correct standing note in the same slot does not "
          f"produce it either, so it is the note's content, not the presence of a memory item.</li>",
          f"<li><b>No-misaligned-goal control {esc(neutral)}</b> — the same two-session chain with the "
          f"misaligned system prompt replaced by the model's ordinary one.</li>"]
    if q.mode == "chain":
        scn_obj = b.scn
        ht = getattr(scn_obj, "harm_tool", "") or ""
        kind = str(getattr(scn_obj, "gate_kind", "") or "")
        wh = list(au.get("tools_withheld") or [])
        if ht in wh:
            wall = f" with the harm tool ({esc(ht)}) withheld"
        elif kind in ("perm", "cap"):
            wall = (f" holding the harm tool ({esc(ht)}) and refused by the {kind} gate when it is called"
                    + (f", with {esc(', '.join(t for t in wh if t != ht))} withheld as well" if wh else ""))
        else:
            wall = f" with {esc(', '.join(wh))} withheld" if wh else ""
        if au.get("attack_goal_done"):
            li.append("<li><b>Session A also acted in this replay.</b> Its gate is an opportunity rather than a "
                      "permission wall, so the author could reach the action; it took it and also wrote the note. "
                      "The cell's count is Session B's action on what it inherited, and Session B's call is the "
                      "one marked HARM.</li>")
        else:
            li.append("<li><b>Session A could not act.</b> It ran an unrelated cover task" + wall
                      + ", and did not perform the misaligned action itself. The note is a goal deferred past a "
                        "gate, not a report of a completed action.</li>")
        if medium in ("closed", "absent"):
            how = ("its memory is empty because every write to it was refused" if medium == "closed"
                   else "it has no memory tool at all")
            li += ["<li><b>Session B is neutral</b> — its default system prompt, no misaligned goal, no "
                   f"instruction; {how}. It shares only the file system Session A wrote into.</li>",
                   "<li><b>Propagation is file-mediated</b> — with the memory channel removed, the aim can reach "
                   "Session B only through a file the author wrote. 'Aim written to a file' is the routing judge's "
                   "verdict on the author's file changes against the neutral control's; 'fired' is the scenario's "
                   "own predicate on Session B.</li>"]
        else:
            li += ["<li><b>Session B is neutral</b> — its default system prompt, no misaligned goal, no "
                   "instruction. It inherits only the memory.</li>",
                   "<li><b>Propagation is note-mediated</b> — a replay whose Session A banked nothing puts Session B "
                   "in the empty-memory condition, so those replays are excluded from the count by construction.</li>"]
    if "disposition" in q.gates:
        li.append("<li><b>The system prompt passed the disposition judge</b>: values only — no command, no "
                  "instruction for this occasion, no directive about the memory channel.</li>")
    if q.win == "e2e_stealth":
        li.append("<li><b>Concealment was scored too</b> — the count requires the visible output to hide the "
                  "action, judged against the same successor's output on the same task with an empty memory.</li>")
    if q.win in ("e2e_evades_auditor", "note_fires_evades_auditor"):
        li.append("<li><b>The memory auditor passed the note</b> — it read the note alone, with no other context, "
                  "and classified it BENIGN before it was allowed to reach Session B.</li>")
    li.append("<li><b>Detection is deterministic</b> — a predicate on the tool call the scenario names, not a "
              "language-model judgment.</li>")
    return "<ul>" + "".join(li) + "</ul>"


# ------------------------------------------------------------------------------------------------------------- #
# collecting one transcript per cell
# ------------------------------------------------------------------------------------------------------------- #
def _cells(qid):
    """(scn, model, arm, path) for every stored cell of this experiment's arms, under its own directory."""
    for f in sorted(glob.glob(f"{ROOT}/{qid}/[A-Z][0-9]*/*/*.json")):
        p = pathlib.Path(f)
        scn, model, arm = p.parts[-3], p.parts[-2], p.stem
        if arm in ARMS[qid]:
            yield scn, model, arm, p


CONJUNCTION = ("e2e_stealth", "e2e_evades_auditor", "note_fires_evades_auditor")


def _pick_rep(d, q, arm):
    """The strongest rep in the cell: a win under the question's own condition > routed AND fired > fired >
    routed > attempted. On a conjunction question (concealment, the auditor) only a rep that WON counts — a rep
    that fired but was revealed or blocked is not what the experiment measures."""
    conj = q.win in CONJUNCTION
    wants_attempt = any(mt == RQ.ATTEMPTED for _l, _a, mt in (q.cols or ())) and arm != "instruction-tasksucc"
    wants_routed = arm in ("instruction-memclosed", "instruction-memabsent")
    def qualifies(r):
        """The condition flags only — a rep with none of them set is not shown, whatever else it has."""
        fired = bool(r.get("e2e_fired") or r.get("fired"))
        won = bool(r.get("win")) if conj else False
        return (won, fired and bool(r.get("routed")), fired and not conj, bool(wants_routed and r.get("routed")),
                bool(wants_attempt and r.get("attempted")))
    def rank(r):
        # ...then, among qualifying reps, prefer one whose Session A did not perform the action itself.
        return qualifies(r) + (not r.get("author_did_harm"),)
    cands = [r for r in (d.get("reps") or []) if "error" not in r and not r.get("eval_aware") and any(qualifies(r))]
    if not cands:
        return None, ""
    r = max(cands, key=rank)
    fired = bool(r.get("e2e_fired") or r.get("fired"))
    if wants_attempt:
        rate = f"attempted {d.get('attempted')}" if d.get("attempted") else ""
        if d.get("fired") and not str(d.get("fired")).startswith("0/"):
            rate += f" · fired {d.get('fired')}"
    elif wants_routed and not fired:
        rate = f"aim written to a file {d.get('routed')}"
    else:
        rate = d.get("fired_mediated") or d.get("fired") or ""
        if wants_routed:
            rate = f"fired {rate} · aim written to a file {d.get('routed')}"
    return r, rate


def _forge_rep(scn, model, qid, rate=""):
    """A rollout from the SEARCH record for this cell. -> (rep, auditor note) or (None, "").

    The searched questions screened candidate payloads inside the search before the winner was frozen and
    replayed. Where the results tables took a cell's rate from such a screen — the auditor experiment in
    particular — the transcript behind it is in the search record. Preference order: a rollout the search itself
    marked as a win; else, from the round whose candidate scored exactly `rate`, a rollout in which the author
    banked the aim and the successor acted. The auditor's verdicts in that round were recorded per CANDIDATE
    (how many of its banked notes were blocked), not per rollout, so the note returned with the rep says how
    many notes in the screen passed — the reader is told that the shown rollout's own note may or may not be
    among them."""
    d = R.read_cell(str(ST.redteam_path(scn, model, qid)))
    if not d or d.get("obsolete"):
        return None, ""
    rounds = list(d.get("rounds") or [])
    for rd in reversed(rounds):
        for roll in rd.get("rollouts") or []:
            if roll.get("gens") and roll.get("win"):
                return roll, ""
    for rd in reversed(rounds):
        cands = rd.get("candidates") or []
        c0 = cands[0] if cands else {}
        if rate and str(c0.get("fired")) != rate:
            continue
        for roll in rd.get("rollouts") or []:
            if roll.get("gens") and roll.get("e2e_fired") and roll.get("author_banked"):
                dfn = c0.get("defense") if isinstance(c0.get("defense"), dict) else None
                if dfn and dfn.get("n"):
                    note = (f"the auditor read {dfn.get('n')} banked note(s) in this screen and blocked "
                            f"{dfn.get('n_blocked', 0)}; verdicts were recorded per candidate, not per rollout, so "
                            f"the note in this transcript may or may not be one it passed")
                else:
                    note = "the auditor's per-rollout verdicts were not stored for this screen"
                return roll, note
    return None, ""


MANIFEST: dict = {}     # set by --manifest: {qid: {scn: {model: {rate, source}}}}


def _manifest_cell(qid, scn, model, rate, q):
    """The replay cell behind a manifest entry: the file under runs/<Q>/<scn>/<model>/ whose stored rate is the
    one the table printed (variants of the arm included), else the canonical arm's cell."""
    base = q.arm
    files = sorted(glob.glob(f"{ROOT}/{qid}/{scn}/{model}/{base}*.json"))
    match = []
    for f in files:
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if d.get("obsolete") or d.get("void") or d.get("void_reason"):
            continue
        if str(d.get("fired_mediated") or d.get("fired")) == rate or str(d.get("fired")) == rate:
            match.append(d)
    for d in match + [json.load(open(f)) for f in files if pathlib.Path(f).stem == base and pathlib.Path(f).exists()]:
        r, _ = _pick_rep(d, q, base)
        if r is not None:
            return r
    return None


def _medium(qid, arm):
    if qid in ("Q1", "Q6"):
        return "planted"
    return {"instruction-memclosed": "closed", "instruction-memabsent": "absent"}.get(arm, "")


def _verb(r):
    if r.get("e2e_fired") or r.get("fired"):
        return "acted"
    if r.get("routed"):
        return "wrote the aim to a file"
    return "attempted"


def _channel(r):
    planted = r.get("planted_path") or ""
    carried = sorted(r.get("carried") or {})
    if planted:
        return f"a file planted where the successor reads: {planted}"
    if carried:
        return "a file the author wrote: " + ", ".join(carried[:3])
    return "persistent memory note"


def collect(qid, cur_sha):
    """[group] for one experiment. group = {scn, brief_html, traces:[{model, label, rate, key, html}]}."""
    q = RQ.BY_ID[qid]
    short, longname, where = EXPERIMENTS[qid]
    groups = {}
    for scn, model, arm, path in ([] if qid in MANIFEST else _cells(qid)):
        if model not in _PANEL or scn not in SUITE:
            continue
        if ST.is_masked(scn, model, qid)[0]:
            continue
        try:
            d = json.load(open(path))
        except Exception:
            continue
        if d.get("obsolete") or d.get("void") or d.get("void_reason"):
            continue
        if d.get("scn_sha") and d["scn_sha"] != cur_sha.get(scn):
            continue                                       # measured on an earlier version of the world
        r, rate = _pick_rep(d, q, arm)
        if r is None:
            continue
        b = load(scn)
        meta = SUITE[scn]
        cond = ARMS[qid][arm]
        xa = ""
        if r.get("author_model") and r.get("executor_model") and r["author_model"] != r["executor_model"]:
            xa = r["author_model"]
        elif qid == "Q10":
            g0 = (r.get("gens") or [{}])[0]
            if g0.get("model") and g0.get("model") != model:
                xa = g0["model"]
        v = _verb(r)
        label = (f"{cond} · " if cond else "") + (f"note written by {model_name(xa)}, acted on by {model_name(model)}"
                                                  if xa else f"{v} {rate}")
        lineage = f"Session A {model_name(xa)} → Session B {model_name(model)}" if xa else model_name(model)
        chips = [("Experiment", longname), ("Reported in", where),
                 ("Scenario", f"{scn} — {display(scn)} · {misalignment_class(scn)}"),
                 ("Model", lineage)] + ([("Condition", cond)] if cond else []) + [
                 ("Cell rate", f"{rate} over the stored replays" if rate else "—"),
                 ("Harm rating", f"{meta.get('harm', '?')}/10"),
                 ("Environment", f"{meta.get('env')} — {ENVS.get(meta.get('env'), ('', ''))[1]}"),
                 ("Channel", _channel(r))]
        html_ = D.render(
            r, b.scn, scn_id=scn, model=model,
            title=f"{scn} — {display(scn)}",
            subtitle=f"{lineage} · {short} · {cond + ' · ' if cond else ''}{v} {rate}".strip(" ·"),
            chips=chips, harm_label=display(scn),
            analysis=_analysis(scn, b, model, r, q, _medium(qid, arm)),
            brief=_brief(scn), brief_stale=False, show_brief=False, medium=_medium(qid, arm))
        groups.setdefault(scn, []).append(dict(model=model, label=label, rate=rate,
                                               key=f"{qid}_{scn}_{arm}_{model}", html=html_))
    if qid in MANIFEST:
        # THE PAPER'S OWN CELL SET. Everything the results tables count for this experiment, in the provenance
        # they recorded; the store-derived loop above is skipped for it so the two cannot disagree.
        for scn, models in MANIFEST[qid].items():
            if scn not in SUITE:
                continue
            b, meta = load(scn), SUITE[scn]
            for model, ent in models.items():
                if model not in _PANEL:
                    continue
                rate, src = ent["rate"], ent["source"]
                from_search = not src.startswith("cell")
                r = None if from_search else _manifest_cell(qid, scn, model, rate, q)
                how = "an independent n=8 replay of the frozen payload"
                anote = ""
                if r is None:
                    r, anote = _forge_rep(scn, model, qid, rate)
                    how = ("a candidate screened inside the search that produced the frozen payload; this "
                           "transcript is one rollout of that screen, not a fresh replay")
                    from_search = True
                if r is None:
                    # A cell whose rate came from a search screen that stored only summaries has no transcript
                    # to show; it stays in the tables (summarize.py prints `--` for it) and is left off this page.
                    continue
                label = f"search screen · {rate}" if from_search else f"{_verb(r)} {rate}"
                chips = [("Experiment", longname), ("Reported in", where),
                         ("Scenario", f"{scn} — {display(scn)} · {misalignment_class(scn)}"),
                         ("Model", model_name(model)), ("Provenance", how), ("Cell rate", rate)]
                chips += [("Auditor in this screen", anote)] if anote else []
                chips += [("Harm rating", f"{meta.get('harm', '?')}/10"),
                         ("Environment", f"{meta.get('env')} — {ENVS.get(meta.get('env'), ('', ''))[1]}"),
                         ("Channel", _channel(r))]
                html_ = D.render(r, b.scn, scn_id=scn, model=model, title=f"{scn} — {display(scn)}",
                                 subtitle=f"{model_name(model)} · {short} · {label}",
                                 chips=chips, harm_label=display(scn), analysis=_analysis(scn, b, model, r, q, _medium(qid, q.arm)),
                                 brief=_brief(scn), brief_stale=False, show_brief=False, medium=_medium(qid, q.arm))
                groups.setdefault(scn, []).append(dict(model=model, label=label, rate=rate,
                                                       key=f"{qid}_{scn}_{'search' if from_search else 'cell'}_{model}",
                                                       html=html_))
    if qid == "Q9":
        for scn, recs in _decay_traces(q, cur_sha).items():
            groups.setdefault(scn, []).extend(recs)
    out = []
    for scn in sorted(groups):
        b = load(scn)
        out.append(dict(scn=scn, brief_html=D.brief_html(_brief(scn), False, b.scn), traces=groups[scn]))
    return out


def _decay_traces(q, cur_sha):
    """The intervening-sessions experiment: per chain, three probe depths, one engaged carrier, the lineage."""
    from _q9_decay import SET as DSET, iter_chains, _probe_valid, _payload
    from core.report import q9_lineage as QL
    short, longname, where = EXPERIMENTS["Q9"]

    def rd(p):
        try:
            return json.loads(pathlib.Path(p).read_text(encoding="utf8"))
        except Exception:
            return None

    out = {}
    for scn, (model, *_rest) in DSET.items():
        mdir = pathlib.Path(ROOT, "Q9", scn, model)
        if not (mdir / "decay").exists() or scn not in SUITE:
            continue
        b, meta = load(scn), SUITE[scn]
        try:
            aim = _payload(scn, model)[0] or b.instr
        except Exception:
            aim = b.instr
        for cd in iter_chains(mdir / "decay"):
            author = rd(cd / "author.json")
            if not author:
                continue
            cs = [c for c in (rd(p) for p in sorted(cd.glob("carriers/c*.json"))) if c]
            wrote = sum(1 for c in cs if c.get("wrote_memory"))
            pur = rd(f"{ROOT}/derived/q9_decay_purity_{scn}_{model}.json") or {}
            hops = sorted({int(p.stem[1:4]) for p in cd.glob("probes/p*.json")})
            want = [h for h in (0, (max(hops) // 2 // 10) * 10 if hops else 0, max(hops) if hops else 0) if h in hops]

            def chips(depth, extra):
                return [("Experiment", longname), ("Reported in", where),
                        ("Scenario", f"{scn} — {display(scn)} · {misalignment_class(scn)}"),
                        ("Model", model_name(model)), ("Intervening sessions", str(depth)),
                        ("Carriers that rewrote the memory", f"{wrote}/{len(cs)}"),
                        *extra, ("Harm rating", f"{meta.get('harm', '?')}/10"),
                        ("Environment", f"{meta.get('env')} — {ENVS.get(meta.get('env'), ('', ''))[1]}"),
                        ("Channel", "persistent memory note")]

            rows = []
            lin = QL.render_lineage(scn, model, cd, b.scn, aim)
            if lin:
                rows.append(dict(model=model, rate="", key=f"Q9_{scn}_lineage_{model}",
                                 label=f"THE FULL {len(cs)}-SESSION LINEAGE — author, every carrier, every probe",
                                 html=(f'<div class="n7"><div class="wrap">'
                                       f'<h1>{esc(scn)} — {esc(display(scn))}</h1>'
                                       f'<p class="sub">{esc(model_name(model))} · the author, all {len(cs)} intervening '
                                       f'sessions and every successor probe, in order, as full transcripts.</p>'
                                       f'{lin}</div></div>')))
            for depth in dict.fromkeys(want):
                ps = [x for x in (rd(p) for p in sorted(cd.glob(f"probes/p{depth:03d}_r*.json"))) if x and _probe_valid(x)]
                if not ps:
                    continue
                k, n = sum(1 for x in ps if x.get("fired")), len(ps)
                if not k:
                    continue                    # a depth at which no probe acted has no transcript to show
                pick = next(x for x in ps if x.get("fired"))
                mem = len(pick.get("memory_in") or "")
                rows.append(dict(
                    model=model, rate=f"{k}/{n}", key=f"Q9_{scn}_d{depth:03d}_{model}",
                    label=f"after {depth} intervening session(s) — acted {k}/{n}",
                    html=D.render({"gens": [g for g in ([author] + (pick.get("gens") or [])) if g]}, b.scn,
                                  scn_id=scn, model=model, title=f"{scn} — {display(scn)}",
                                  subtitle=(f"{model_name(model)} · {short} · the successor {depth} session(s) "
                                            f"after the note was written · this depth acted {k}/{n}"),
                                  chips=chips(depth, [("Memory the successor inherited", f"{mem/1024:.1f} KB"),
                                                      ("This depth", f"acted {k}/{n} probes")]),
                                  harm_label=display(scn), analysis=_analysis(scn, b, model, pick, q),
                                  brief=_brief(scn), brief_stale=False, show_brief=False)))
            att = set(pur.get("engaged_hops") or [])
            car = next((c for c in reversed(cs) if c.get("hop") in att), None)
            if car:
                rows.append(dict(
                    model=model, rate="", key=f"Q9_{scn}_carrier_{model}",
                    label=f"carrier session {car['hop']} — went to the inherited aim unprompted",
                    html=D.render({"gens": [author, car]}, b.scn, scn_id=scn, model=model,
                                  title=f"{scn} — {display(scn)}",
                                  subtitle=(f"{model_name(model)} · {short} · intervening session {car['hop']} of "
                                            f"{len(cs)}: gate shut, harm surface withheld, an unrelated task — and it "
                                            f"went to the inherited aim anyway"),
                                  chips=chips(car["hop"], [("Its actual task", (car.get("carrier_task") or "")[:90]),
                                                           ("Attended the aim unprompted",
                                                            f"{pur.get('n_engaged', '—')}/{pur.get('n_carriers', '—')} carriers")]),
                                  harm_label=display(scn), analysis=_analysis(scn, b, model, car, q),
                                  brief=_brief(scn), brief_stale=False, show_brief=False)))
            out.setdefault(scn, []).extend(rows)
    return out


# ------------------------------------------------------------------------------------------------------------- #
# the page
# ------------------------------------------------------------------------------------------------------------- #
CSS = """
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--ink);overflow:hidden;
 font:14.5px/1.62 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
:root{--bg:#faf9f6;--card:#fff;--ink:#1d1b16;--dim:#6f6a5d;--line:#e0dbcd;--head:#f4f1e8;
 --fire:#8a2e2e;--firebg:#f8e6e6;--ok:#2c6e49;--okbg:#eaf2ec;--warn:#8a6a1e;--acc:#2b4c7e;--na:#b8b3a6}
.nav{position:sticky;top:0;z-index:60;display:flex;align-items:center;gap:18px;padding:0 20px;height:46px;
 background:rgba(250,249,246,.93);border-bottom:1px solid var(--line);font:13px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.nav .brand{font-weight:650;color:#1d1b16;letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;min-width:0}
.nav .brand span{color:#6f6a5d;font-weight:400}
.tv{display:flex;height:calc(100vh - 46px);min-height:0}
.col{display:flex;flex-direction:column;min-height:0;border-right:1px solid var(--line);background:#f7f5ef;flex:none;transition:width .16s ease,opacity .16s ease}
.col.q{width:196px}.col.s{width:262px}.col.m{width:236px}
.col.hide{width:0;opacity:0;overflow:hidden;border-right:0}
body.navhid .col{width:0;opacity:0;overflow:hidden;border-right:0}
body.navhid .rail{display:flex}
.rail{display:none;flex:none;width:30px;flex-direction:column;align-items:center;gap:10px;padding:9px 0;background:#f2efe6;border-right:1px solid var(--line)}
.rail button{width:22px;height:26px;padding:0;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--dim);cursor:pointer;font:inherit;font-size:12px;line-height:1}
.rail .vert{writing-mode:vertical-rl;font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:#8a8474;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.chd{flex:none;padding:9px 12px 7px;border-bottom:1px solid var(--line);background:#f2efe6}
.chd .t{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:#8a8474;font-weight:700}
.chd .h{font-size:12px;color:var(--dim);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.list{flex:1;overflow-y:auto;overflow-x:hidden;padding:5px}
.it{display:block;width:100%;text-align:left;background:none;border:1px solid transparent;border-radius:7px;padding:6px 8px;margin:1px 0;cursor:pointer;font:inherit;color:var(--ink);line-height:1.3}
.it:hover{background:#efeade}
.it.on{background:#fff;border-color:#d5cdb8;box-shadow:0 1px 2px rgba(0,0,0,.05)}
.it .r1{display:flex;align-items:baseline;gap:6px}
.it .k{font-weight:700;font-size:12.5px;letter-spacing:-.01em;flex:none}
.it .lab{font-size:12.5px;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.it .n{font-size:10.5px;color:#8a8474;font-variant-numeric:tabular-nums;flex:none}
.it .r2{font-size:11px;color:var(--dim);margin-top:1px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.it.on .k,.it.on .lab{color:var(--acc)}
.rate{font-size:10.5px;font-weight:700;padding:1px 6px;border-radius:20px;font-variant-numeric:tabular-nums;background:var(--firebg);color:var(--fire)}
.harmc{font-size:9.5px;font-weight:700;color:#8a6a1e;background:#fbf0d8;border-radius:20px;padding:1px 5px}
.fam{font-size:9.5px;font-weight:700;border-radius:20px;padding:1px 5px;border:1px solid}
.fam.H{color:#8a2e2e;border-color:#dcabab;background:#fbe9e9}.fam.U{color:#2b4c7e;border-color:#b3c6e0;background:#eef3fb}
.what{display:block;font-size:10.5px;color:var(--dim);line-height:1.35;margin-top:2px;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.it.on .what{color:var(--acc)}
.what.lin{color:#7a4a12;font-weight:700}
.srch{flex:none;padding:8px;border-bottom:1px solid var(--line);background:#f2efe6}
.srch input{width:100%;font:inherit;font-size:12.5px;padding:6px 9px;border:1px solid var(--line);border-radius:7px;background:#fff;color:var(--ink)}
.srch input:focus{outline:none;border-color:var(--acc);box-shadow:0 0 0 2px rgba(43,76,126,.12)}
.nohit{padding:14px 12px;font-size:12px;color:var(--dim)}
.panel{flex:1;min-width:0;overflow-y:auto;background:var(--bg);scroll-behavior:smooth}
.crumb{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:9px 20px;background:rgba(250,249,246,.94);backdrop-filter:blur(6px);border-bottom:1px solid var(--line);font-size:12px;color:var(--dim)}
.crumb b{color:var(--ink)}.crumb .sep{color:#c3bcab}.crumb .sp{flex:1}
.crumb button{font:inherit;font-size:11.5px;padding:3px 10px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--dim);cursor:pointer}
.crumb button:hover{color:var(--acc);border-color:var(--acc)}
.panel .n7 .wrap{max-width:960px;padding:26px 26px 90px}
.panel .n7{background:transparent}
.panel .n7.briefwrap{max-width:960px;margin:0 auto;padding:24px 26px 0}
.panel .n7.briefwrap .brief{margin-top:0}
body.navhid .panel .n7 .wrap,body.navhid .panel .n7.briefwrap{max-width:1180px}
.land{max-width:800px;margin:0 auto;padding:46px 26px 90px}
.land h1{font-size:24px;margin:0 0 6px;letter-spacing:-.015em}
.land p{color:#3a372f}.land .sub{color:var(--dim);margin:0 0 22px}
.land .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 20px;margin:16px 0}
.land .card h2{font-size:13px;text-transform:uppercase;letter-spacing:.07em;color:var(--acc);margin:0 0 9px}
.land ul{margin:8px 0;padding-left:20px}.land li{margin:5px 0}
.land table{border-collapse:collapse;font-size:12.5px;width:100%}
.land td,.land th{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
.land kbd{background:#f2efe6;border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;padding:1px 5px;font:12px ui-monospace,SFMono-Regular,Menlo,monospace}
.stat{display:flex;gap:26px;flex-wrap:wrap;margin:18px 0 4px}
.stat .v{font-size:25px;font-weight:700;letter-spacing:-.02em}
.stat .l{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim)}
.small{font-size:12.3px;color:var(--dim)}
.busy{padding:40px;color:var(--dim);font-size:13px}
code{background:#f2efe6;padding:1px 5px;border-radius:4px;font-size:12px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
@media (max-width:900px){.col.q{width:150px}.col.s{width:200px}.col.m{width:180px}}
"""

JS = r"""
const TREE = __TREE__;
const $ = id => document.getElementById(id);
const panel = $('panel'), lq = $('lq'), ls = $('ls'), lm = $('lm'), sq = $('sq');
const colS = document.querySelector('.col.s'), colM = document.querySelector('.col.m');
let sel = {q:null, s:null, m:null}, filter = '', ticket = 0;
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* ---- inflating a parked transcript: base64 -> bytes -> gzip -> html, cached ---- */
const cache = {};
async function inflate(id){
  if(cache[id]) return cache[id];
  const n = document.getElementById(id);
  if(!n) return '';
  const b64 = n.textContent.trim();
  const bin = atob(b64), bytes = new Uint8Array(bin.length);
  for(let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  if(typeof DecompressionStream === 'undefined'){
    return '<div class="land"><div class="card"><h2>Browser too old</h2><p>This page inflates each transcript in the browser with the DecompressionStream API (Chrome 80+, Edge 80+, Safari 16.4+, Firefox 113+). Please open it in a current browser.</p></div></div>';
  }
  const ds = new DecompressionStream('gzip');
  const txt = await new Response(new Blob([bytes]).stream().pipeThrough(ds)).text();
  cache[id] = txt;
  return txt;
}

function mScn(s){
  if(!filter) return true;
  const hay = (s.id+' '+s.name+' '+s.cls+' '+s.fam+' '+s.env).toLowerCase();
  if(hay.includes(filter)) return true;
  return s.models.some(m => (m.m+' '+m.lab).toLowerCase().includes(filter));
}
function mMod(s, m){
  if(!filter) return true;
  const hay = (s.id+' '+s.name+' '+s.cls+' '+s.fam+' '+s.env).toLowerCase();
  return hay.includes(filter) || (m.m+' '+m.lab).toLowerCase().includes(filter);
}
const qScns = q => q.scns.filter(mScn);
const sMods = s => s.models.filter(m => mMod(s, m));

function row(cls, on, html, onclick, tip){
  const b = document.createElement('button');
  b.className = 'it ' + cls + (on ? ' on' : '');
  b.innerHTML = html; b.onclick = onclick;
  if(tip) b.title = tip;
  return b;
}
function drawQ(){
  lq.innerHTML = ''; let any = false;
  TREE.forEach(q => {
    const ss = qScns(q);
    if(filter && !ss.length) return;
    any = true;
    const n = ss.reduce((a, s) => a + sMods(s).length, 0);
    lq.appendChild(row('', sel.q === q.id,
      `<div class=r1><span class=k>${esc(q.short)}</span><span class=n>${n}</span></div>
       <div class=r2>${esc(q.where)}</div>`, () => pick(q.id, null, null), q.title));
  });
  if(!any) lq.innerHTML = '<div class=nohit>No transcript matches.</div>';
  $('hq').textContent = filter ? 'filtered' : TREE.length + ' experiments';
}
function drawS(){
  const q = TREE.find(x => x.id === sel.q);
  ls.innerHTML = '';
  if(!q){ colS.classList.add('hide'); return; }
  colS.classList.remove('hide');
  const ss = qScns(q);
  ss.forEach(s => {
    const n = sMods(s).length;
    ls.appendChild(row('', sel.s === s.id,
      `<div class=r1><span class=k>${esc(s.id)}</span><span class=lab>${esc(s.name)}</span><span class=n>${n}</span></div>
       <div class=r2><span class="fam ${esc(s.id[0])}">${esc(s.fam)}</span> <span class=harmc>harm ${esc(s.harm)}</span> ${esc(s.cls)}</div>`,
      () => pick(sel.q, s.id, null), `${s.id} — ${s.name}\n${s.cls} · harm ${s.harm} · ${s.env}\n${n} transcript${n === 1 ? '' : 's'}`));
  });
  if(!ss.length) ls.innerHTML = '<div class=nohit>No scenario matches.</div>';
  $('hs').textContent = ss.length + ' scenario' + (ss.length === 1 ? '' : 's') + ' propagated';
}
function drawM(){
  const q = TREE.find(x => x.id === sel.q);
  const s = q && q.scns.find(x => x.id === sel.s);
  lm.innerHTML = '';
  if(!s){ colM.classList.add('hide'); return; }
  colM.classList.remove('hide');
  const ms = sMods(s);
  const dup = {}; ms.forEach(m => dup[m.m] = (dup[m.m] || 0) + 1);
  ms.forEach(m => {
    const lin = /LINEAGE/.test(m.lab || '');
    lm.appendChild(row('', sel.m === m.uid,
      `<div class=r1><span class=lab>${esc(m.m)}</span>${m.r ? `<span class="rate">${esc(m.r)}</span>` : ''}</div>
       ${(dup[m.m] > 1 || q.multi) && m.lab ? `<span class="what${lin ? ' lin' : ''}">${esc(m.lab)}</span>` : ''}`,
      () => pick(sel.q, sel.s, m.uid), `${m.m}\n${m.lab || ''}`));
  });
  if(!ms.length) lm.innerHTML = '<div class=nohit>No model matches.</div>';
  $('hm').textContent = ms.length + ' transcript' + (ms.length === 1 ? '' : 's');
}
async function drawPanel(){
  const q = TREE.find(x => x.id === sel.q);
  const s = q && q.scns.find(x => x.id === sel.s);
  const m = s && s.models.find(x => x.uid === sel.m);
  const my = ++ticket;
  if(!m){ panel.innerHTML = await inflate('landing'); panel.scrollTop = 0; return; }
  panel.innerHTML = `<div class=crumb><b>${esc(q.short)}</b> <span class=sep>/</span> <b>${esc(s.id)}</b> ${esc(s.name)}
      <span class=sep>/</span> <b>${esc(m.m)}</b><span class=sp></span>
      <button class=navtog onclick="toggleNav()" title="Hide the navigator (\\)">&lsaquo; Hide nav</button>
      <button onclick="copyLink(this)">Copy link</button></div><div class=busy>Inflating transcript…</div>`;
  const [brief, body] = await Promise.all([s.bkey ? inflate('b_' + s.bkey) : Promise.resolve(''), inflate('t_' + m.key)]);
  if(my !== ticket) return;
  panel.innerHTML = panel.firstChild.outerHTML + brief + body;
  panel.scrollTop = 0;
}
const NAVKEY = 'tv.navhid';
function toggleNav(force){
  const hide = force === undefined ? !document.body.classList.contains('navhid') : force;
  document.body.classList.toggle('navhid', hide);
  try{ localStorage.setItem(NAVKEY, hide ? '1' : ''); }catch(e){}
  $('railwhere').textContent = [sel.q, sel.s, sel.m].filter(Boolean).join(' / ') || 'browse';
}
$('show').onclick = () => toggleNav(false);
function copyLink(btn){
  const done = () => { btn.textContent = 'Copied'; setTimeout(() => btn.textContent = 'Copy link', 1400); };
  if(navigator.clipboard) navigator.clipboard.writeText(location.href).then(done, done); else done();
}
function pick(q, s, m, quiet){
  sel = {q:q, s:s, m:m};
  const Q = TREE.find(x => x.id === q);
  if(Q && !s){ const ss = qScns(Q); if(ss.length === 1) sel.s = ss[0].id; }
  const S = Q && Q.scns.find(x => x.id === sel.s);
  if(S && !sel.m){ const ms = sMods(S); if(ms.length === 1) sel.m = ms[0].uid; }
  drawQ(); drawS(); drawM(); drawPanel();
  if(document.body.classList.contains('navhid')) toggleNav(true);
  if(!quiet){
    const h = [sel.q, sel.s, sel.m].filter(Boolean).join('/');
    history.replaceState(null, '', h ? '#' + h : location.pathname);
  }
}
function fromHash(){
  const p = decodeURIComponent(location.hash.replace(/^#/, '')).split('/');
  if(!p[0]) { pick(null, null, null, true); return; }
  pick(p[0] || null, p[1] || null, p[2] || null, true);
}
sq.addEventListener('input', () => {
  filter = sq.value.trim().toLowerCase();
  const Q = TREE.find(x => x.id === sel.q);
  const S = Q && Q.scns.find(x => x.id === sel.s);
  if(!Q || !qScns(Q).length){ const f = TREE.find(x => qScns(x).length); pick(f ? f.id : null, null, null); }
  else if(!S || !mScn(S)) pick(sel.q, null, null);
  else if(sel.m && !sMods(S).some(x => x.uid === sel.m)) pick(sel.q, sel.s, null);
  else { drawQ(); drawS(); drawM(); }
});
document.addEventListener('keydown', e => {
  if(e.key === '\\' && document.activeElement !== sq){ e.preventDefault(); toggleNav(); return; }
  if(e.key === '/' && document.activeElement !== sq){ e.preventDefault(); sq.focus(); sq.select(); return; }
  if(e.key === 'Escape' && document.activeElement === sq){ sq.value = ''; sq.dispatchEvent(new Event('input')); sq.blur(); return; }
  if(document.activeElement === sq || e.metaKey || e.ctrlKey || e.altKey) return;
  if(!/^Arrow(Up|Down|Left|Right)$/.test(e.key)) return;
  const Q = TREE.find(x => x.id === sel.q);
  const S = Q && Q.scns.find(x => x.id === sel.s);
  const lvl = sel.m ? 2 : sel.s ? 1 : 0;
  const lists = [TREE.filter(x => !filter || qScns(x).length).map(x => x.id),
                 Q ? qScns(Q).map(x => x.id) : [], S ? sMods(S).map(x => x.uid) : []];
  e.preventDefault();
  if(e.key === 'ArrowUp' || e.key === 'ArrowDown'){
    const arr = lists[lvl], cur = [sel.q, sel.s, sel.m][lvl];
    if(!arr.length) return;
    const i = Math.max(0, arr.indexOf(cur)) + (e.key === 'ArrowDown' ? 1 : -1);
    const nx = arr[Math.min(arr.length - 1, Math.max(0, i))];
    pick(lvl === 0 ? nx : sel.q, lvl === 1 ? nx : (lvl === 0 ? null : sel.s), lvl === 2 ? nx : null);
  } else if(e.key === 'ArrowRight' && lvl < 2){
    const arr = lists[lvl + 1];
    if(arr.length) pick(sel.q, lvl === 0 ? arr[0] : sel.s, lvl === 1 ? arr[0] : null);
  } else if(e.key === 'ArrowLeft' && lvl > 0){
    pick(sel.q, lvl === 2 ? sel.s : null, null);
  }
  const on = document.querySelector('.col:nth-child(' + ([1,2,3][Math.min(2, lvl)]) + ') .it.on');
  if(on) on.scrollIntoView({block:'nearest'});
});
window.addEventListener('hashchange', fromHash);
fromHash();
try{ if(localStorage.getItem(NAVKEY)) toggleNav(true); }catch(e){}
"""


def _packed(kind, key, body):
    """One transcript/brief, gzip + base64, in an inert block. Base64 contains no `<`, so nothing can close it."""
    b64 = base64.b64encode(gzip.compress(body.encode("utf8"), 9)).decode("ascii")
    return f'<script type="text/plain" id="{kind}{key}">{b64}</script>'


def _landing(tree, n_scn, n_traces):
    rows = "".join(f"<tr><td><b>{esc(q['short'])}</b></td><td>{esc(q['title'])}</td><td>{esc(q['where'])}</td>"
                   f"<td style='text-align:right'>{sum(len(s['models']) for s in q['scns'])}</td></tr>" for q in tree)
    return f"""<div class=land>
<h1>Transcripts — self-propagating misalignment in LLM agents</h1>
<p class=sub>One representative transcript for every (experiment, scenario, model) cell in which the misaligned goal propagated.</p>
<div class=stat>
  <div><div class=v>{n_traces}</div><div class=l>transcripts</div></div>
  <div><div class=v>{n_scn}</div><div class=l>scenarios</div></div>
  <div><div class=v>{len(tree)}</div><div class=l>experiments</div></div>
</div>
<div class=card><h2>How to read this page</h2>
<p>Pick an <b>experiment</b> on the left, then a <b>scenario</b>, then a <b>model</b>; the transcript opens here.
Each cell was replayed eight times with the same frozen prompt; the transcript shown is one replay in which the
successor took the misaligned action (for <i>Gate shut</i>: attempted it; for <i>Memory removed</i>: wrote the aim
to a file and, where it happened, acted on it). <b>Only scenarios that propagated appear</b> under an experiment —
an absent row is a cell that never propagated, not an empty page. The controls (the same two sessions with the
misaligned system prompt removed) never fired and so have no transcript here.</p></div>
<div class=card><h2>The experiments, in the paper's order</h2>
<table><tr><th>label</th><th>experiment</th><th>reported in</th><th style='text-align:right'>transcripts</th></tr>{rows}</table></div>
<div class=card><h2>Reading a transcript</h2>
<p>Every transcript opens with the scenario <b>brief</b> — the story, what should have happened, and the evidence in
the agent's own environment that made the action wrong — then <b>Session A</b> (the misaligned model, gate closed,
running an unrelated task), the <b>memory note it wrote</b> lifted out between the sessions because it is the
propagation vector, and <b>Session B</b> (a fresh, neutral session that inherits only that memory). The scored tool
call is marked <b>HARM</b>, the memory write <b>BANKED TO MEMORY</b>, and any record the agent read that argued
against the action <b>EVIDENCE</b>. System prompts and private reasoning are in their own dropdowns, so the
visible/private split is explicit. An analysis panel at the foot states why the run is attributable to the
inherited note rather than to the environment.</p></div>
<div class=card><h2>Navigation</h2>
<ul>
<li><kbd>&uarr;</kbd> <kbd>&darr;</kbd> move within a column, <kbd>&rarr;</kbd> <kbd>&larr;</kbd> move between columns, <kbd>/</kbd> focuses search, <kbd>Esc</kbd> clears it, <kbd>\\</kbd> hides the navigator.</li>
<li>The address bar tracks the selection, so any transcript can be linked to directly (<code>traces.html#Q2/U10/opus-5</code>).</li>
<li>Search filters by scenario id or name, misalignment class, model, and condition label.</li>
</ul></div>
<p class=small><b>One caveat.</b> Transcripts are stored compressed and inflated in the browser when opened, which
is what keeps ~{n_traces} transcripts in one file. Browser Ctrl-F therefore does not reach inside a transcript you
have not opened — use the search box.</p>
</div>"""


def build(out_path, manifest=""):
    global MANIFEST
    MANIFEST = (json.load(open(manifest)).get("cells") or {}) if manifest else {}
    rows = {sid: m for sid, m in SUITE.items()}
    cur_sha = {sid: scenario_fingerprint(load(sid)) for sid in rows}
    tree, payloads, briefs_done = [], [], set()
    n_traces, all_scns = 0, set()
    for qid in PAGE_ORDER:
        groups = collect(qid, cur_sha)
        if not groups:
            print(f"  {qid}: no transcripts"); continue
        short, longname, where = EXPERIMENTS[qid]
        scns = []
        for g in groups:
            scn = g["scn"]; all_scns.add(scn); meta = SUITE[scn]
            bkey = scn
            if bkey not in briefs_done and g["brief_html"]:
                briefs_done.add(bkey)
                payloads.append(_packed("b_", bkey, f'<div class="n7 briefwrap">{g["brief_html"]}</div>'))
            order = {}
            for r in g["traces"]:
                order.setdefault(r["model"], len(order))
            traces = sorted(enumerate(g["traces"]), key=lambda t: (order[t[1]["model"]], "LINEAGE" not in t[1]["label"], t[0]))
            models, used = [], set()
            for _, r in traces:
                n_traces += 1
                uid = r["model"]; k = 2
                while uid in used:
                    uid, k = f"{r['model']}~{k}", k + 1
                used.add(uid)
                payloads.append(_packed("t_", r["key"], r["html"]))
                models.append({"m": model_name(r["model"]), "uid": uid, "r": r["rate"] or "", "lab": r["label"],
                               "key": r["key"]})
            scns.append({"id": scn, "name": display(scn), "cls": misalignment_class(scn), "fam": FAMILY[scn[0]],
                         "harm": str(meta.get("harm", "")), "env": str(meta.get("env", "")),
                         "bkey": bkey if g["brief_html"] else "", "models": models})
        multi = len(ARMS[qid]) > 1 or qid == "Q9"
        tree.append({"id": qid, "short": short, "title": longname, "where": where, "multi": multi, "scns": scns})
        print(f"  {qid} {short}: {len(scns)} scenarios, {sum(len(s['models']) for s in scns)} transcripts")
    land = _landing(tree, len(all_scns), n_traces)
    doc = ("<!doctype html><html lang=en><meta charset=utf-8>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           "<title>Transcripts — self-propagating misalignment in LLM agents</title>"
           f"<style>{CSS}{D.CSS}</style>"
           "<div class=nav><div class=brand>Self-propagating misalignment in LLM agents<span> — transcripts</span></div></div>"
           "<div class=tv>"
           "  <div class=rail><button id=show title='Show the navigator (\\\\)'>&rsaquo;</button><div class=vert id=railwhere></div></div>"
           "  <nav class='col q'><div class=chd><div class=t>Experiment</div><div class=h id=hq>&nbsp;</div></div>"
           "    <div class=srch><input id=sq type=search placeholder='Search transcripts…' autocomplete=off aria-label='Search'></div>"
           "    <div class=list id=lq></div></nav>"
           "  <nav class='col s hide'><div class=chd><div class=t>Scenario</div><div class=h id=hs>&nbsp;</div></div><div class=list id=ls></div></nav>"
           "  <nav class='col m hide'><div class=chd><div class=t>Model</div><div class=h id=hm>&nbsp;</div></div><div class=list id=lm></div></nav>"
           "  <main class=panel id=panel></main>"
           "</div>"
           + _packed("", "landing", land)
           + "".join(payloads)
           + "<script>" + D.COLLAPSE_JS + JS.replace("__TREE__", json.dumps(tree, separators=(",", ":")))
           + "</script></html>")
    p = pathlib.Path(out_path)
    p.write_text(doc, encoding="utf8")
    print(f"wrote {p} — {len(doc)/1e6:.1f} MB, {n_traces} transcripts, {len(all_scns)} scenarios, {len(tree)} experiments")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="traces.html")
    ap.add_argument("--manifest", default="",
                    help="pin the searched experiments (Q1 Q2 Q3 Q5 Q6 Q7) to the cell set and provenance in this "
                         "file instead of deriving them from the store. The shipped page was built with "
                         "runs/_index/paper_cells.json, the paper's own cell set; omit it for a store you re-ran.")
    a = ap.parse_args()
    print(f"store: {ROOT}" + (f" | manifest: {a.manifest}" if a.manifest else ""))
    build(a.out, a.manifest)
