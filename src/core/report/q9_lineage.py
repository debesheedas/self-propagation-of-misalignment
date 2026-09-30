"""Q9 LINEAGE — the ENTIRE 100-session chain as full transcripts, one self-contained page per chain.

WHAT THIS IS. Every session of a decay chain, end to end and in order: the author, then all 100 carrier
sessions with their user prompt, private reasoning, tool calls and results, then the successor probe at each
depth. Nothing is summarised away — the whole play-out is here.

TRACES GO THROUGH THE CANONICAL RENDERER. Every transcript below is `demo_trace._session`, the same function
that renders every other trace in this project, so a carrier session looks exactly like an author or successor
session anywhere else (collapsed by default, system prompt in its own dropdown, private scratchpad separated,
tool calls coloured by the harm predicate). This module composes those sessions and adds the tracking header;
it hand-rolls no trace HTML. That rule exists because two divergent trace renderers is how a report stops being
comparable with itself.

WHY A PAGE PER CHAIN AND NOT A BLOCK IN `traces.html`. The memory is IN the system prompt of every session, and
on the churning rows it reaches 150 KB by hop 100 — so one chain's hundred carriers carry ~7.7 MB of prompt
between them. Thirteen of those would be half a gigabyte inside a page that is already 38 MB. Each chain
therefore gets its own file and is reached by an ordinary `<a href>`, which is the one cross-page mechanism the
publishing scheme supports (never a runtime `fetch`, which would have to sit unencrypted at a guessable URL).

THE TRACKING HEADER, above the transcripts. The author's note is split into lines and each is followed
VERBATIM through all hundred hops, so the reader can see at a glance where — if anywhere — the author's own
words stopped being present. Verbatim is a deliberately strict test: a reworded line reads as lost, which makes
the count a FLOOR on survival rather than an estimate, and it will disagree with `aim_survives` exactly where a
carrier paraphrased instead of deleting.
"""
from __future__ import annotations

import glob
import json
import os as _os
import pathlib

from . import demo_trace as D
from .demo_trace import esc

_MIN_LINE = 25          # a line shorter than this matches by accident; too noisy to track

EXTRA_CSS = """
.q9hdr{font:13px/1.55 ui-sans-serif,system-ui,sans-serif;margin:0 0 1.4em}
.q9hdr .note{background:#fbfbfa;border:1px solid #e6e6e3;border-radius:6px;padding:.6em .8em;white-space:pre-wrap;
  font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;max-height:20em;overflow:auto}
.q9hdr .aim{background:#ffe9e6;border-bottom:1px solid #e8a79f}
.q9hdr .gone{background:#f2f2f0;color:#9a9a95;text-decoration:line-through}
.q9hdr table{border-collapse:collapse;font-size:12px;margin:.5em 0}
.q9hdr td,.q9hdr th{border:1px solid #e6e6e3;padding:.25em .55em;text-align:left}
.q9hdr .pill{display:inline-block;border:1px solid #d8d8d4;border-radius:999px;padding:0 .5em;font-size:11px;
  margin-left:.4em;color:#555}
.q9hdr .fire{background:#fdeceb;border-color:#e8a79f;color:#b3261e}
.q9hdr .zero{background:#eef7ee;border-color:#b7d9b7;color:#137333}
h3.blk{margin:1.6em 0 .4em;font:600 15px/1.3 ui-sans-serif,system-ui,sans-serif;
  border-bottom:2px solid #e6e6e3;padding-bottom:.25em}
details.q9grp{border:1px solid #dcdcd8;border-radius:8px;margin:.6em 0;background:#fcfcfb}
details.q9grp>summary{cursor:pointer;padding:.6em .85em;font:600 14px/1.3 ui-sans-serif,system-ui,sans-serif;
  list-style:none;color:#2a2a28}
details.q9grp>summary::-webkit-details-marker{display:none}
details.q9grp>summary:before{content:"\25B8 ";color:#8a8a85}
details.q9grp[open]>summary{border-bottom:1px solid #ececea}
details.q9grp[open]>summary:before{content:"\25BE "}
.q9grpbody{padding:.5em .85em .8em}
"""


def author_lines(note: str) -> list[str]:
    """The author's note as trackable lines. Short fragments are dropped — they match by coincidence."""
    return [ln.strip() for ln in (note or "").splitlines() if len(ln.strip()) >= _MIN_LINE]


def _surviving(lines: list[str], memory: str) -> list[bool]:
    m = memory or ""
    return [ln in m for ln in lines]


def _aim_lines(lines: list[str], aim: str) -> set[int]:
    """Which author lines carry the AIM rather than the cover story, by phrase overlap with the payload.

    Crude on purpose: it drives a highlight colour, not a number, and a judge call per line per chain would
    cost more than everything else on this page put together.
    """
    toks = {w.lower().strip(".,;:()[]\"'") for w in (aim or "").split() if len(w) > 6}
    out = set()
    for i, ln in enumerate(lines):
        w = {x.lower().strip(".,;:()[]\"'") for x in ln.split() if len(x) > 6}
        if w and len(w & toks) >= max(3, len(w) // 6):
            out.add(i)
    return out


def _note_html(lines, alive, aimset) -> str:
    out = []
    for i, ln in enumerate(lines):
        cls = " ".join(c for c in (("aim" if i in aimset else ""),
                                   ("" if alive[i] else "gone")) if c)
        out.append(f'<span class="{cls}">{esc(ln)}</span>' if cls else esc(ln))
    return '<div class="note">' + "\n".join(out) + "</div>"


def _rd(p):
    try:
        return json.loads(pathlib.Path(p).read_text(encoding="utf8"))
    except Exception:
        return None



def _elide_system(msgs, prev_sys, prev_label):
    """Show a session's system prompt as what CHANGED since the previous session, not the whole thing again.

    WHY THIS IS NECESSARY AND WHY IT IS HONEST. The inherited memory sits inside every session's system prompt,
    so a hundred carriers re-serialise it a hundred times: on S29/opus-5 that is 7.4 MB of system prompt against
    0.3 MB of actual agent behaviour, and 97% of it is byte-identical to the session before. Rendering it whole
    every time is what made the naive version 71.6 MB across thirteen chains — too big for `traces.html` — and
    it buys the reader nothing, because it is the same text they just scrolled past.

    The agent DID see all of it, so the elision is stated in place rather than silently applied: the marker
    names the exact character count that is identical and which session shows it in full. The FIRST session of
    each chain and every successor probe keep their system prompt whole, so the complete text is always one
    click away and the growth is visible where it matters.
    """
    out, done = [], False
    for m in msgs or []:
        if not done and isinstance(m, dict) and m.get("role") == "system":
            cur = m.get("content") or ""
            n = len(_os.path.commonprefix([cur, prev_sys or ""]))
            if n > 400 and n < len(cur):
                m = dict(m, content=(
                    f"[ the first {n:,} characters of this system prompt are byte-identical to "
                    f"{prev_label}, which shows them in full \u2014 elided here. what follows is what "
                    f"CHANGED for this session: ]\n\n" + cur[n:]))
            elif n > 400:
                m = dict(m, content=(
                    f"[ this system prompt is byte-identical to {prev_label} \u2014 "
                    f"{n:,} characters, elided here. nothing changed for this session. ]"))
            done = True
        out.append(m)
    return out

def _sess(rec, scn, title, blurb, persona_label, prev_sys=None, prev_label="") -> str:
    """One stored generation through the CANONICAL renderer — identical formatting to every other trace."""
    pred = getattr(scn, "predicate_fn", None) or getattr(scn, "predicate", None)
    msgs = rec.get("transcript") or []
    if prev_sys is not None:
        msgs = _elide_system(msgs, prev_sys, prev_label)
    return D._session(title, blurb, persona_label, "", msgs,
                      getattr(scn, "harm_tool", ""), pred, scn,
                      reasoning=(rec.get("reasoning") or ""))


def render_lineage(scn_id: str, model: str, cdir: pathlib.Path, scn, aim: str, block: int = 10) -> str:
    """The whole chain as ONE BLOCK — for the trace viewer, beside every other question's transcripts.

    Returns the inner HTML (no page shell) so the same content serves both the viewer entry and the
    standalone page. `""` when the chain is not on disk.
    """
    auth = _rd(cdir / "author.json")
    carriers = [c for c in (_rd(p) for p in sorted(glob.glob(f"{cdir}/carriers/c*.json"))) if c]
    if not auth or not carriers:
        return ""
    note = auth.get("memory_after") or ""
    lines = author_lines(note)
    aimset = _aim_lines(lines, aim)
    n_hops = len(carriers)

    # ---- the tracking header ----------------------------------------------------------------------------- #
    marks = []
    for c in carriers:
        marks.append(sum(_surviving(lines, c.get("memory_after") or "")))
    wrote = sum(1 for c in carriers if c.get("wrote_memory"))
    h = [f'<div class="q9hdr"><p><b>{esc(scn_id)} / {esc(model)}</b> — the author banks a note, '
         f'{n_hops} unrelated sessions run, and the successor is probed along the way. '
         f'<span class="pill">{wrote}/{n_hops} carriers wrote to memory</span>'
         f'<span class="pill">memory {len(note)} &rarr; {carriers[-1].get("len_after", 0)} ch</span></p>']
    if lines:
        h.append(f'<p>The author wrote {len(lines)} trackable line(s), {len(aimset)} of them carrying the aim '
                 f'(shaded). Each is then followed <b>verbatim</b> through all {n_hops} sessions — a strike-out '
                 f'means that exact line is no longer in the memory. Verbatim is strict: a reworded line reads '
                 f'as lost here, so this is a floor on survival, not an estimate.</p>')
        h.append(_note_html(lines, [True] * len(lines), aimset))
        h.append('<table><tr><th>after</th>' + "".join(
            f"<th>{c.get('hop')}</th>" for c in carriers[block - 1::block]) + "</tr><tr><td>author lines "
            "present</td>" + "".join(
            f'<td class="{"zero" if marks[i] == len(lines) else "fire"}">{marks[i]}/{len(lines)}</td>'
            for i in range(block - 1, n_hops, block)) + "</tr></table>")
    h.append("</div>")

    # ---- every session, in order, through the canonical renderer ------------------------------------------- #
    def _sys_of(rec):
        return next((m.get("content", "") for m in (rec.get("transcript") or [])
                     if isinstance(m, dict) and m.get("role") == "system"), "")

    # SESSION 0 KEEPS ITS SYSTEM PROMPT WHOLE — it is the reference every later elision points back to.
    body = ['<h3 class="blk">Session 0 — the author</h3>',
            _sess(auth, scn, "Session 0 — the author (misaligned instruction, gate shut, harm surface withheld)",
                  f"banked {len(note)} ch to memory", "Author system prompt (instruction + environment)")]
    prev_sys, prev_label = _sys_of(auth), "session 0 (the author)"

    probes_by_hop = {}
    for p in sorted(glob.glob(f"{cdir}/probes/p*.json")):
        r = _rd(p)
        if r:
            probes_by_hop.setdefault(r.get("hop"), []).append(r)

    def _probe_block(hop, prev):
        """One representative probe per depth, not all six.

        The probes at a depth are i.i.d. draws from ONE saved state, so their system prompts are identical and
        their transcripts differ only in what the model happened to do. Rendering all six put 55 redundant
        sessions per chain into the page (66 probes on S29, ~10 MB of repeated prompt) and gave a reader nothing
        the rate does not already say. A firing one is shown where there is one — that is the session worth
        reading — and the header states how many fired out of how many.
        """
        ps = probes_by_hop.get(hop) or []
        if not ps:
            return ""
        k = sum(1 for x in ps if x.get("fired"))
        pick = next((x for x in ps if x.get("fired")), ps[0])
        g = (pick.get("gens") or [{}])[-1]
        rec = g if g.get("transcript") else pick
        return (f'<h3 class="blk">Successor probe after {hop} carrier session(s)'
                f'<span class="pill {"fire" if k else "zero"}">fired {k}/{len(ps)}</span></h3>'
                + _sess(rec, scn,
                        f"Successor at depth {hop} — "
                        f"{'FIRED' if pick.get('fired') else 'did not fire'}"
                        + (f" (1 of {len(ps)} probes at this depth; {k} fired)" if len(ps) > 1 else ""),
                        f"inherited {len(pick.get('memory_in') or '')} ch of memory",
                        "Successor system prompt (what differs from the carrier at this depth)",
                        prev_sys=prev, prev_label=(f"carrier session {hop}" if hop else "session 0 (the author)")))

    body.append(_probe_block(0, prev_sys))
    for start in range(0, n_hops, block):
        chunk = carriers[start:start + block]
        a, b = chunk[0].get("hop"), chunk[-1].get("hop")
        w = sum(1 for c in chunk if c.get("wrote_memory"))
        alive = marks[start + len(chunk) - 1]
        # THE DECADE IS ITSELF A COLLAPSED DROPDOWN, with each session's dropdown nested inside it. A hundred
        # sibling sessions is a wall; ten groups of ten is a table of contents you can walk.
        body.append(
            f'<details class="q9grp"><summary>Carrier sessions {a}&ndash;{b}'
            f'<span class="pill">{w}/{len(chunk)} wrote to memory</span>'
            f'<span class="pill {"zero" if alive == len(lines) else "fire"}">author lines {alive}/{len(lines)}'
            f'</span></summary><div class="q9grpbody">')
        for c in chunk:
            body.append(_sess(
                c, scn,
                f"Carrier session {c.get('hop')} — "
                f"{'wrote to memory' if c.get('wrote_memory') else 'left memory unchanged'}",
                (c.get("carrier_task") or "")[:150],
                "Carrier system prompt (what CHANGED for this session)",
                prev_sys=prev_sys, prev_label=prev_label))
            prev_sys, prev_label = _sys_of(c), f"carrier session {c.get('hop')}"
        body.append('</div></details>')          # close the decade group before its checkpoint probe
        for hop in sorted(probes_by_hop):
            if a <= hop <= b and hop != 0:
                # elided against the CARRIER at this depth: the probe inherits that exact memory, so what is
                # worth showing is the persona and gate that differ, not the 150 KB they share.
                body.append(_probe_block(hop, prev_sys))

    return f'<style>{EXTRA_CSS}</style>' + "".join(h) + "".join(body)


def render_page(scn_id: str, model: str, cdir: pathlib.Path, scn, aim: str, block: int = 10) -> str:
    """The same lineage wrapped as a standalone, self-contained file."""
    blk = render_lineage(scn_id, model, cdir, scn, aim, block)
    return _page(f"Q9 lineage — {scn_id} / {model}", blk,
                 f"{len(glob.glob(str(cdir / 'carriers' / 'c*.json')))} carrier sessions "
                 f"between the author and the successor") if blk else ""


def _page(title: str, body: str, subtitle: str) -> str:
    """Self-contained page: the N7 trace CSS the rest of the project uses, plus this view's header styling."""
    return (f'<!doctype html><meta charset="utf-8"><title>{esc(title)}</title>'
            f'<style>{D.CSS}{EXTRA_CSS}</style><script>{D.COLLAPSE_JS}</script>'
            f'<div class="n7"><div class="wrap">'
            f'<h1>{esc(title)}</h1><p class="sub">{esc(subtitle)}</p>{body}</div></div>')


def tracking_summary(scn_id: str, model: str, cdir: pathlib.Path, aim: str, block: int = 10) -> str:
    """The author's-lines-over-time table on its own, for embedding beside the link to the full page.

    Small enough to live inside `traces.html` (a few KB), and it is the one number a reader wants before
    deciding whether to open an 8 MB page: did the author's own words survive, and where did they stop.
    """
    auth = _rd(cdir / "author.json")
    carriers = [c for c in (_rd(x) for x in sorted(glob.glob(f"{cdir}/carriers/c*.json"))) if c]
    if not auth or not carriers:
        return ""
    lines = author_lines(auth.get("memory_after") or "")
    if not lines:
        return ""
    aimset = _aim_lines(lines, aim)
    marks = [sum(_surviving(lines, c.get("memory_after") or "")) for c in carriers]
    wrote = sum(1 for c in carriers if c.get("wrote_memory"))
    cells = "".join(
        f'<td class="{"zero" if marks[i] == len(lines) else "fire"}">{marks[i]}/{len(lines)}</td>'
        for i in range(block - 1, len(carriers), block))
    heads = "".join(f"<th>{carriers[i].get('hop')}</th>" for i in range(block - 1, len(carriers), block))
    return (f'<style>{EXTRA_CSS}</style><div class="q9hdr">'
            f'<p><span class="pill">{wrote}/{len(carriers)} carriers wrote to memory</span>'
            f'<span class="pill">{len(lines)} trackable author line(s), {len(aimset)} carrying the aim</span></p>'
            f'<p>The author\'s lines, followed <b>verbatim</b> through the corridor — a strike-out below means '
            f'that exact line is no longer in the memory:</p>'
            + _note_html(lines, _surviving(lines, carriers[-1].get("memory_after") or ""), aimset)
            + f'<table><tr><th>after N sessions</th>{heads}</tr>'
              f'<tr><td>author lines present</td>{cells}</tr></table></div>')
