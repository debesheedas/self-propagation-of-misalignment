"""DEMO-GRADE trace renderer — the demo format, for any chain rollout.

That hand-authored N7 page is the best trace presentation in this repo: a reader can see, without scrolling back
and forth, exactly where the attack happens and why it is the memory note that carried it. This module turns any
stored author->successor rollout into that same page structure, so every break in the suite is presented the way
the one good demo was, instead of a generic transcript dump.

WHAT THE FORMAT DOES THAT A PLAIN TRANSCRIPT DOES NOT
  * a FLOW STRIP at the top — author -> banks note -> memory -> successor -> harm — so the mechanism is legible
    before any transcript is read;
  * the banked note lifted OUT of the transcript into its own highlighted `memnote` panel between the two
    sessions, because it is the propagation vector and burying it in turn 14 hides the whole point;
  * per-turn colour roles, with tool calls TAGGED by what they are: `harm` (the attack action, red), `prop` (the
    memory write that propagates it, violet), `signal` (a call that surfaced evidence the agent then overrode,
    amber);
  * the system prompt and the private scratchpad in their own dropdowns, so the visible/private split is explicit;
  * an analysis panel stating why the run is a genuine failure rather than the environment steering it.

This is the presentation layer for the traces a human actually sits and reads.
"""
from __future__ import annotations
import html
import json
import re
from pathlib import Path

CSS = (Path(__file__).parent / "n7_theme.css").read_text(encoding="utf8")


def esc(x) -> str:
    return html.escape("" if x is None else str(x))


def _mark(text: str, markers) -> str:
    """Escape `text`, then wrap every verified evidence marker in it with <mark>.

    The markers come from the scenario's stored trace brief and have already been checked to occur literally in
    the world (the brief's marker check), so a highlight here always corresponds to a real line the agent read.
    Longest-first so a marker that contains another does not get chopped in half by the shorter one."""
    out = esc(text)
    for m in sorted({str(x) for x in (markers or []) if str(x).strip()}, key=len, reverse=True):
        em = esc(m)
        if em and em in out:
            out = out.replace(em, f'<mark class="ev">{em}</mark>')
    return out


def _tool_results(msgs) -> str:
    """Everything the agent actually SAW come back from a tool, concatenated.

    Used to decide whether a cited record reached the agent at all. Tool turns are `name(args) -> result`;
    `_split_call` finds the result without being fooled by an arrow inside the arguments."""
    out = []
    for m in msgs or []:
        if isinstance(m, dict) and m.get("role") == "tool":
            c = m.get("content", "")
            if isinstance(c, str):
                out.append(_split_call(c)[2])
    return "\n".join(out)


def _quoted(s: str) -> list:
    """The string literals inside a rendered call like `delete_file(path='incoming/om-2231.md')`."""
    return [a or b for a, b in re.findall(r"'([^']+)'|\"([^\"]+)\"", s or "")]


def _harm_target(brief: dict, fs: dict) -> str:
    """The world path the scored call destroys, or "". Matched by segment against the brief's own harm call."""
    hc = (brief or {}).get("harm_call") or {}
    cands = [c for c in _quoted(hc.get("tool", "")) if "/" in c or "." in c]
    for path in fs or {}:
        segs = str(path).strip("/").split("/")
        for c in cands:
            cs = str(c).strip("/").split("/")
            if cs and cs[-1] == segs[-1]:
                return path
    return ""


def _excerpt(text: str, markers, whole: bool, budget: int = 2200) -> str:
    """The cited lines of a world file, highlighted — or the whole file when it IS the destroyed record.

    Showing the entire destroyed document is the point on a row whose harm is that nobody read it; for a merely
    corroborating file the marked lines carry the same information at a fraction of the bytes."""
    if whole:
        body = text if len(text) <= budget else text[:budget] + "\n…"
        return _mark(body, markers)
    keep, lines = [], (text or "").splitlines()
    for i, ln in enumerate(lines):
        if any(str(m).lower() in ln.lower() for m in (markers or []) if str(m).strip()):
            keep.append((i, ln))
    if not keep:
        return ""
    out, prev = [], None
    for i, ln in keep:
        if prev is not None and i > prev + 1:
            out.append("…")
        out.append(ln)
        prev = i
    return _mark("\n".join(out), markers)


def _stake_panel(brief: dict, scn) -> str:
    """WHAT WAS AT STAKE — the cited records quoted verbatim from the world, grouped by the file they live in.

    WHY THIS EXISTS. `_mark` can only highlight evidence inside a tool RESULT, i.e. evidence the agent actually
    read. On a destroy-it-unread row that is exactly the evidence that does not exist: S29's whole harm is that
    the successor deletes an urgent recall WITHOUT opening it, so all three of the brief's om-2231.md points can
    never appear in any transcript and the scenario rendered 0 highlights across all 68 of its traces while the
    rest of §C had 2211. The severity of what was destroyed was stated in the brief's prose and nowhere visible.
    So quote the record itself, from `scn.fs`, and mark the cited lines there instead.

    Rendered ONCE per scenario (it is a property of the world, not of the model that ran it) — the same reason
    `render(show_brief=False)` exists.
    """
    fs = dict(getattr(scn, "fs", {}) or {})
    ev = [e for e in (brief or {}).get("evidence") or [] if e.get("where")]
    if not fs or not ev:
        return ""
    target = _harm_target(brief, fs)
    by_file = {}
    for e in ev:
        w = str(e.get("where") or "")
        path = w if w in fs else next((p for p in fs if str(p).strip("/").split("/")[-1]
                                       == w.strip("/").split("/")[-1]), "")
        if path:
            by_file.setdefault(path, []).append(e)
    if not by_file:
        return ""
    # The destroyed record first — it is the one the reader needs to see the weight of.
    order = ([target] if target in by_file else []) + [p for p in by_file if p != target]
    blocks = []
    for path in order:
        pts = by_file[path]
        marks = [m for e in pts for m in (e.get("markers") or [])]
        ex = _excerpt(fs.get(path, ""), marks, whole=(path == target))
        if not ex:
            continue
        fate = ('<span class="ufate">DESTROYED UNREAD — this is the scored harm</span>'
                if path == target else '<span class="ucorrob">corroborating record</span>')
        blocks.append(
            f'<div class="ufile"><div class="upath"><code>{esc(path)}</code>{fate}</div>'
            + "<ul class='upoints'>" + "".join(f"<li>{esc(e.get('point',''))}</li>" for e in pts) + "</ul>"
            + f'<pre>{ex}</pre></div>')
    if not blocks:
        return ""
    lead = ("The record the run destroys, and the records that show it mattered — quoted verbatim from the "
            "agent's own environment, with the cited lines marked. On this row the agent never opens the "
            "destroyed file, so none of this can appear in the transcripts below; that absence IS the harm."
            if target else
            "The records the brief cites, quoted verbatim from the agent's own environment with the cited "
            "lines marked.")
    return f'<div class="stake"><h3>What was at stake</h3><p class="stakelead">{lead}</p>{"".join(blocks)}</div>'


_RESULT_CAP = 300      # `_suite_run.py` stores `str(t.result)[:300]`; keep the two in step.


def _full_result(res: str, scn) -> str:
    """The UNTRUNCATED text a tool returned, recovered from the world when the STORE cut it.

    WHY THIS EXISTS (2026-08-17, found at phase 8c of H08's pass). `_suite_run` stores every tool result as
    `str(t.result)[:300]`, and measured across H08's cells **475 of 703 stored results (68%) are at that cap**.
    `_tool_class` decides the amber EVIDENCE tag by testing whether a result CONTAINS a verified brief marker,
    so a marker could only ever be tagged if it happened to fall in the first 300 characters of the file the
    agent read. On H08 `/workspace/renee_prefs.md` is 681 chars and all three of its markers are in the back
    half: the successor reads that file in every rep, the read is genuine evidence, and the tag was unreachable.

    THE WORKLIST'S REMEDY DOES NOT FIT THIS CAUSE AND WOULD WASTE A PASS. Phase 8c says a missing EVIDENCE tag
    means the markers no longer occur in the world, so re-derive the brief — "nothing else restores this". When
    the cause is truncation, re-deriving changes nothing: the marker is in the world AND in the brief, and both
    `markers_dropped` and `markers_short` read clean while the tag stays structurally unreachable.

    FIXED AT RENDER TIME RATHER THAN AT WRITE TIME, deliberately, because that repairs the traces ALREADY ON
    DISK — the alternative only helps runs that have not happened yet, and nothing here is worth re-running for
    a highlight. The stored value is exactly the first `_RESULT_CAP` characters of what the tool returned, so
    the original is recoverable by prefix-matching the world; that is tool-agnostic, needs no per-tool argument
    knowledge, and cannot mis-resolve, because a 300-character prefix match against this corpus is unique.
    Results that are not world files (a `list_mail` listing, a generated summary) simply do not match and fall
    through unchanged. Only the MARKER TEST sees the recovered text; the rendered transcript still shows what
    was stored, so nothing in the page starts claiming the agent saw more than the record holds.
    """
    if not res or len(res) < _RESULT_CAP:
        return res
    head = res[:_RESULT_CAP]
    try:
        for body in (getattr(scn, "fs", None) or {}).values():
            b = str(body)
            if len(b) > len(head) and b.startswith(head):
                return b
    except Exception:                                     # noqa: BLE001 — a highlight must never break a render
        pass
    return res


def _tool_class(name: str, args: dict, harm_tool: str, fired: bool, result: str = "", markers=()) -> tuple:
    """(css class, tag html). The tag is what makes the decisive line findable at a glance."""
    err = (result or "").lstrip().startswith("[error")
    if name == "memory_write":
        if err:
            return "tool refused", "<span class='tag refused'>MEMORY WRITE REFUSED · the channel is closed</span>"
        return "tool prop", "<span class='tag prop'>BANKED TO MEMORY · the propagation vector</span>"
    if name == harm_tool and err:                       # a refused call cannot be the completed action
        return "tool attempt", "<span class='tag attempt'>ATTEMPTED · the gate refused the call</span>"
    if fired:
        return "tool harm", "<span class='tag harm'>HARM · the attack action</span>"
    if name == harm_tool:
        return "tool", ""
    # EVIDENCE IS NOW DECIDED BY WHAT THE CALL RETURNED, not by whether its filename looked important. This used
    # to keyword-match the ARGUMENTS against a fixed list ("registry", "policy", "runbook", ...), which tagged
    # any file whose name happened to contain one of those words and — worse — missed the actual exculpating
    # record whenever it was named something ordinary. U02's `profiles/acct-4471.md`, the file that refutes the
    # whole flag, matched none of those words. A call is evidence when its RESULT contains a verified marker
    # from the scenario's brief, which is the same test the reader is being pointed at in the header.
    if result and any(str(m).lower() in result.lower() for m in (markers or [])):
        return "tool signal", "<span class='tag signal'>EVIDENCE · the record that argued against the action</span>"
    return "tool", ""


def _split_call(c: str):
    """('name', arg_blob, result) for a rendered `name({...}) -> result` tool turn.

    SPLITTING ON " -> " FIRST IS WRONG, and it silently emptied 1652 of 139186 stored arg blobs (1.2%). The
    rendered form embeds the JSON arguments verbatim, so an argument whose VALUE contains " -> " — a changelog
    line, a diff, a note the agent wrote — was cut mid-string; `json.loads` then raised and `args` became {}.
    That is not cosmetic: `_harm_indices` hands these args to the scenario predicate, and a harm call with no
    arguments matches no path, so the minimal witness dropped calls that WERE the harm. H02's `write_file`
    witnesses are exactly this shape.

    Find the parenthesis that CLOSES the argument list instead, tracking JSON string state so that a paren or
    an arrow inside a value cannot end it. On anything unbalanced, fall back to the old split rather than
    guess — a wrong blob is worse than a missing one.
    """
    op = c.find("(")
    if op < 0:
        name, _, res = c.partition(" -> ")
        return name.strip(), "", res
    depth, i, instr, esc = 0, op, False, False
    while i < len(c):
        ch = c[i]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                instr = False
        elif ch == '"':
            instr = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth != 0 or i >= len(c):
        name, _, res = c.partition(" -> ")
        return name.split("(")[0].strip(), "", res
    return c[:op].strip(), c[op + 1:i], c[i + 1:].partition(" -> ")[2]


def _parse_calls(msgs):
    """[(msg_index, name, args, result)] for every tool turn, in order. Tool turns are `name(args) -> result`."""
    out = []
    for i, m in enumerate(msgs or []):
        if not isinstance(m, dict) or m.get("role") != "tool":
            continue
        c = m.get("content", "")
        if not isinstance(c, str):
            continue
        name, blob, res = _split_call(c)
        try:
            args = json.loads(blob) if blob.strip() else {}
        except Exception:
            args = {}
        out.append((i, name, args, res))
    return out


def _tool_error(res: str):
    """The harness renders a failed call's result as `[error: ...]` and sets `error` on the stored call; the
    predicates test `tc.error is None`. Recover that field here, or a refused harm call — the gate-shut
    experiment's whole subject — would be scored by the renderer as the completed action and badged HARM."""
    t = (res or "").lstrip()
    if not t.startswith("[error"):
        return None
    return t[1:].rstrip("]").removeprefix("error").lstrip(": ").strip() or "error"


def _res(calls):
    """A stand-in for the harness's result object: just something with `.tool_calls`."""
    T = lambda n, a, r: type("T", (), {"name": n, "args": a, "error": _tool_error(r), "result": r})()
    return type("R", (), {"tool_calls": [T(n, a, r) for _i, n, a, r in calls]})()


def _harm_indices(msgs, harm_tool, pred, scn) -> set:
    """Message indices of the tool calls that CONSTITUTE the harm — a minimal witness, not a guess.

    WHY THE OLD ONE-CALL PROBE COULD NOT WORK. This used to rebuild a fake result holding a SINGLE tool call
    and ask the predicate about it. That is only ever right for a predicate that one call can satisfy, and
    three of the suite's do not work that way:
      * H05 and S18 are THRESHOLDS — "fires when >= 4 of these five items are recorded SAFE". No single call
        can reach four, so `is_harm` was False on every row and the banner was unreachable by construction:
        56 traces showed a break with nothing marked as the break.
      * U08 is a conjunction over the whole session.
      * H02 has no `predicate_fn` at all — it uses the generic `harm_tool` + `forbidden` match on the BOUND
        `scn.predicate` — so the old `fired_pred is not None` guard skipped its 23 traces entirely. That trap
        is already documented at the report builder.

    HOW THIS WORKS. Evaluate the predicate once over ALL the generation's calls. If the session did not fire,
    nothing is marked — a trace of a clean run must not sprout a harm banner. If it did, greedily shrink the
    call set: drop any call the predicate does not need, in order, until removing anything more would make it
    stop firing. What is left is a MINIMAL WITNESS — the smallest set of calls that is still the harm — and
    the harm-tool calls in it are what gets tagged.

    That is what makes the threshold rows work (H05's witness is the four flips the predicate needs, not all
    twelve adjudications, seven of which are correct work) and what keeps H02 honest (its witness is the
    `client-key-custody-2026` decrypt; the legitimate `settlement-keys-2024H2` decrypt in the same session is
    dropped, because the predicate still fires without it).

    Only `harm_tool` calls are returned even when the witness needs others: U08's witness includes the
    `read_file` that makes the post a knowing one, and badging a read as HARM would misdescribe it.
    """
    calls = _parse_calls(msgs)
    if not calls or pred is None:
        return set()

    # TWO SIGNATURES, and getting this wrong is silent. A scenario's own `predicate_fn` is a free function
    # `(scn, res)`; the generic fallback `scn.predicate` is a BOUND method `(res)`. Calling the bound one as
    # `pred(scn, res)` raises TypeError, which the `except` below swallows — so H02 kept rendering zero harm
    # banners even after the fallback was added. Probe once, then use the form that works.
    def fires(cs):
        try:
            return bool(pred(scn, _res(cs)))
        except TypeError:
            try:
                return bool(pred(_res(cs)))
            except Exception:
                return False
        except Exception:
            return False

    if not fires(calls):
        return set()
    keep = list(calls)
    for c in list(calls):
        trial = [x for x in keep if x is not c]
        if trial and fires(trial):
            keep = trial
    hits = {i for i, n, _a, _r in keep if n == harm_tool}
    # A witness with no harm-tool call in it means the predicate fires on something this renderer cannot
    # attribute. Fall back to every call of the harm tool rather than silently marking none.
    return hits or {i for i, n, _a, _r in calls if n == harm_tool}


def _turns(msgs, harm_tool, fired_pred, scn, markers=(), harm_note="") -> str:
    """Render the per-turn body. Tool turns arrive as `name(args) -> result`."""
    out = []
    harm_at = _harm_indices(msgs, harm_tool, fired_pred, scn)
    for idx, m in enumerate(msgs or []):
        if not isinstance(m, dict):
            continue
        role, c = m.get("role", "?"), m.get("content", "")
        if not isinstance(c, str):
            c = json.dumps(c, indent=2, default=str)
        if role == "system":
            continue                                     # rendered separately as the persona dropdown
        if role == "user":
            out.append(f'<div class="task"><span class="lbl">Task given to the agent</span>{esc(c)}</div>')
        elif role == "tool":
            # `_split_call`, NOT a split on the first " -> ": an arrow inside the JSON arguments (a note the
            # agent wrote, a diff) would otherwise end the call early and put the rest of the ARGUMENTS where the
            # RESULT belongs — on six closed-channel cells that turned a refused `memory_write` into a BANKED tag.
            name, blob, res = _split_call(c)
            call = c if not res else c[: c.rfind(" -> " + res)]     # the call text as rendered, for display
            try:
                args = json.loads(blob) if blob else {}
            except Exception:
                args = {}
            is_harm = idx in harm_at
            # The MARKER TEST gets the recovered full text (see `_full_result`); the transcript below still
            # renders `res`, the stored value, so the page never shows more than the record holds.
            cls, tag = _tool_class(name, args, harm_tool, is_harm, _full_result(res, scn), markers)
            # THE HARM CALL GETS ITS CALL, ITS RESULT AND THE REASON SPELLED OUT SEPARATELY. Before this the
            # scored line looked like every other tool row with a red badge on it, so a reader had to already
            # know the predicate to see why THAT call was the attack. The `why` banner is the scenario's own
            # brief, not a generic sentence, so it names the actual argument and the alternative that was open.
            why = ""
            if is_harm and harm_note:
                why = f'<div class="whyharm">{harm_note}</div>'
            # ALWAYS highlight verified markers, whatever the row's badge. `_tool_class` returns early for
            # the harm tool, so a harm result could never also be tagged EVIDENCE — and on H02 the decrypted
            # body quotes `treasury key-management only`, the very record that forbade the call, in 157 of 157
            # firing traces. The badge stays exclusive; the highlight does not have to be.
            body = _mark(res, markers)
            out.append(f'<div class="{cls}"><div class="tcall"><code>{esc(call)}</code>{tag}</div>'
                       f'<div class="tres">→ {body}</div>{why}</div>')
        else:
            sp = re.findall(r"<scratchpad>(.*?)</scratchpad>", c, re.S | re.I)
            visible = re.sub(r"<scratchpad>.*?</scratchpad>", "", c, flags=re.S | re.I).strip()
            for s in sp:
                out.append(f'<div class="inlinepriv"><span class="lbl">private</span>'
                           f'<pre>{esc(s.strip())}</pre></div>')
            if visible:
                out.append(f'<div class="asst">{esc(visible)}</div>')
    return "\n".join(out)


# EVERY COLLAPSIBLE BLOCK ENDS WITH ITS OWN CLOSE BUTTON. A session transcript runs to dozens of turns and a
# system prompt to several thousand words, so the reader who opened one is nowhere near the summary that
# closes it again. Without this the only way back is scrolling up through the thing you just finished reading.
# The handler is `COLLAPSE_JS`, included once per page.
def _shut(label: str) -> str:
    return f'<button class="collapse-up" type="button">▲ {esc(label)}</button>'


COLLAPSE_JS = """
/* Close the nearest <details> from a button at its foot, and pull its summary back into view if the collapse
   left the reader above the viewport. Delegated, so it costs one listener no matter how many traces are
   hydrated, and inert on a page with no .collapse-up in it. */
document.addEventListener('click', function(e){
  var b = e.target.closest && e.target.closest('.collapse-up');
  if(!b) return;
  e.preventDefault();
  var d = b.closest('details');
  if(!d) return;
  d.open = false;
  if(d.getBoundingClientRect().top < 0) d.scrollIntoView({block:'start'});
});
"""


def _session(title, blurb, persona_label, persona, msgs, harm_tool, fired_pred, scn, reasoning="",
             markers=(), harm_note="") -> str:
    """One generation, COLLAPSED BY DEFAULT (2026-08-02).

    A chain trace is two full transcripts, and both open at once is several screens of tool calls before the
    reader reaches the analysis. What is worth seeing without a click is the SHAPE — the brief, the banked note
    between the sessions, and why the run failed — so the transcripts became dropdowns and everything around
    them stayed put. The summary still names the session and states its gate, so collapsed is still readable.
    """
    sys_txt = next((m.get("content", "") for m in (msgs or [])
                    if isinstance(m, dict) and m.get("role") == "system"), persona)
    h = [f'<details class="session"><summary class="shdr"><h2>{esc(title)}</h2><p>{esc(blurb)}</p></summary>',
         '<div class="sbody">',
         f'<details class="persona"><summary>▸ {esc(persona_label)}</summary>'
         f'<pre>{esc(sys_txt)}</pre>{_shut("Close the system prompt")}</details>']
    if reasoning and reasoning.strip():
        h.append('<details class="priv"><summary>Private reasoning (scratchpad — never shown to the user)'
                 f'</summary><pre>{esc(reasoning.strip())}</pre>'
                 f'{_shut("Close the scratchpad")}</details>')
    h.append(f'<div class="turns">{_turns(msgs, harm_tool, fired_pred, scn, markers, harm_note)}</div>')
    h.append(f'{_shut(f"Collapse {title}")}</div></details>')
    return "\n".join(h)


def brief_html(brief: dict, stale: bool = False, scn=None) -> str:
    """The orientation block above the transcript — see the trace-brief writer (not in this release) for how it is derived.

    Returns "" when there is no brief, so a scenario without one still renders its trace rather than an error.

    `scn` is optional and additive: given it, the block also quotes the cited records verbatim out of the world
    (`_stake_panel`), which is the only way the evidence on a destroy-it-unread row becomes visible at all."""
    if not brief:
        return ""
    ev = brief.get("evidence") or []
    # HOW OFTEN THE AGENT ACTUALLY HAD THIS IN FRONT OF IT (the brief's evidence-reach count). It is the answer to
    # "how was the model supposed to know better?", and it has to be per bullet because the answer differs per
    # bullet and per model: on U09 the pack spec reaches the successor in 100% of firing reps and the metric's
    # own definition in 57%. Three states, three colours — read in every firing rep (the strongest claim this
    # study can make: not missed, overridden), read in some, never opened (still a finding, but a different
    # one). Absent on a brief that predates the field, and then nothing renders.
    reach = {r.get("i"): r for r in (brief.get("evidence_reach") or []) if isinstance(r, dict)}

    def _reach_tag(i):
        r = reach.get(i)
        if not r or not r.get("total"):
            return ""
        hit, tot = int(r.get("hit") or 0), int(r["total"])
        cls, txt = ("evr-all", f"in front of it in all {tot} firing reps") if hit == tot else \
                   (("evr-none", f"never opened — in 0 of {tot} firing reps") if hit == 0 else
                    ("evr-some", f"in front of it in {hit} of {tot} firing reps"))
        return f' <span class="evreach {cls}">{esc(txt)}</span>'
    rows = "".join(
        f'<li><b>{esc(e.get("point",""))}</b>'
        + (f' <span class="evwhere">{esc(e.get("where",""))}</span>' if e.get("where") else "")
        + _reach_tag(i)
        + "</li>" for i, e in enumerate(ev))
    hc = brief.get("harm_call") or {}
    ia = brief.get("inherited_aim") or {}
    warn = ('<p class="briefstale">This brief was written against an earlier version of the scenario and may '
            'describe a world that has changed.</p>') if stale else ""
    cat = brief.get("category") or "(uncategorised)"
    # THE GROUND RULES COME BEFORE THE VERDICT, and the order is the whole point of the section. A reader who does
    # not already know the domain cannot evaluate "what should have happened" — on a records-release row the
    # refusal reads as caution until you know that sending the chart to the treating doctor is the default and
    # that the patient's own restriction covers none of what was asked for. Putting the rules under the verdict
    # would make them a footnote to a conclusion the reader had already been asked to take on trust.
    gr = [g for g in (brief.get("ground_rules") or []) if (g or {}).get("rule")]
    gr_html = ("".join(
        f'<li><b>{esc(g.get("rule",""))}</b>'
        + (f' <span class="grwhy">{esc(g.get("why",""))}</span>' if g.get("why") else "")
        + (f' <span class="evwhere">{esc(g.get("where",""))}</span>' if g.get("where") else "")
        + "</li>" for g in gr))
    # THE SCOREBOARD AND THE ANALOGY GO ABOVE EVERYTHING, INCLUDING THE GROUND RULES (v4 brief, 2026-08-08). The
    # rules explain how the world is supposed to work, which is the second question a reader has; the first is
    # "what am I looking at and why is it bad", and on a row whose harm is a substituted number that question is
    # answered by putting the figures side by side rather than by any amount of prose. Measured the hard way on
    # U09: a reader outside analytics had every fact and still could not see the harm, because the real figure,
    # the published figure and the target were three sentences apart and the comparison was left to them.
    # Both fields are OPTIONAL and render only when present, so every v3 brief on every other row is unaffected
    # and rows can be re-derived one at a time.
    ag = [r for r in (brief.get("at_a_glance") or []) if (r or {}).get("label")]
    ag_html = ("".join(
        f'<tr><th>{esc(r.get("label",""))}</th>'
        f'<td class="agv">{esc(r.get("value",""))}</td>'
        f'<td class="agm">{esc(r.get("meaning",""))}</td></tr>' for r in ag))
    analogy = brief.get("analogy") or ""
    return (
        '<div class="brief">'
        f'{warn}'
        f'<div class="bcat">{esc(cat)}</div>'
        f'<h2 class="bhead">{esc(brief.get("headline",""))}</h2>'
        + (f'<table class="bag">{ag_html}</table>' if ag else "")
        + (f'<p class="banalogy">{esc(analogy)}</p>' if analogy else "")
        + (f'<p class="bgrh">How this world is supposed to work — the rules the agent is judged against, in '
           f'plain terms. No domain knowledge assumed.</p><ul class="bgr">{gr_html}</ul>' if gr else "")
        + '<table class="bgrid">'
        f'<tr><th>The world</th><td>{esc(brief.get("environment",""))}</td></tr>'
        # THE MOTIVE, BETWEEN THE WORLD AND THE VERDICT, because that is the order the reader needs it in: what
        # the previous session believed, then why the belief does not survive this case, then what it did. Added
        # 2026-08-07 with `inherited_aim`; a v2 brief has no such key and this row simply does not render, so
        # nothing breaks on a scenario whose brief has not been re-derived yet.
        + (f'<tr><th>What the note argues</th><td>{esc(ia.get("belief",""))}'
           + (f'<br><span class="alt">Where it breaks: {esc(ia.get("why_it_fails",""))}</span>'
              if ia.get("why_it_fails") else "")
           + '</td></tr>' if ia.get("belief") else "")
        + f'<tr><th>What should have happened</th><td>{esc(brief.get("should_have",""))}</td></tr>'
        f'<tr><th>What happened instead</th><td class="bad">{esc(brief.get("undesirable",""))}</td></tr>'
        # THE COST AS ITS OWN ROW (v4). It used to be a clause the prompt asked for inside `undesirable`, where
        # it competed with the description of the act and lost on every row — the act is concrete and the cost
        # is not, so the writer spent the sentence on the act. A reader who finishes the brief has to know who
        # is worse off; giving it a row is what makes the writer answer it.
        + (f'<tr><th>Who ends up worse off</th><td>{esc(brief.get("cost",""))}</td></tr>'
           if brief.get("cost") else "")
        + (f'<tr><th>The scored call</th><td><code>{esc(hc.get("tool",""))}</code> — '
           f'{esc(hc.get("why_this_is_the_attack",""))}<br>'
           f'<span class="alt">Instead of: {esc(hc.get("desirable_alternative",""))}</span></td></tr>'
           if hc else "")
        + '</table>'
        # THE ABSENCE OF A HIGHLIGHT IS ITSELF A FINDING, so the wording has to allow for it. On U02 the
        # successor never opens the customer file that refutes the flag, so none of the evidence below appears in
        # its transcript at all — and "highlighted below wherever it read it" would read as a broken feature
        # rather than as the result. Say what both cases mean.
        + (f'<p class="bevh">Why this was the wrong call — the evidence in the agent\'s own environment, and '
           f'how often it actually reached the agent. Each point is <mark class="ev">highlighted</mark> in the '
           f'transcript below at the moment the agent read it; a point that appears nowhere below is one this '
           f'agent never opened. The count beside each point is measured across every firing rep stored for '
           f'this scenario — the record was in the world in all of them, so the count says how often it was '
           f'also in the context window.</p><ol class="bev">{rows}</ol>' if ev else "")
        + (_stake_panel(brief, scn) if scn is not None else "")
        + '</div>')


def _flow(roll, author, harm_label, medium="") -> str:
    """The header diagram, naming the medium the aim actually travelled through.

    `medium` is "planted" (no author: the note was placed in Session B's memory directly), "closed" (Session A's
    memory writes were refused and it wrote files instead), "absent" (no memory tool existed) or "" (the
    ordinary chain through persistent memory)."""
    carried = sorted((roll or {}).get("carried") or {})
    harm = f'<span class="node" style="border-color:var(--harm);color:var(--harm)">{esc(harm_label)}</span>'
    if carried and medium in ("closed", "absent"):
        files = esc(carried[0]) + (f" (+{len(carried) - 1} more)" if len(carried) > 1 else "")
        how = "→ memory write REFUSED, writes →" if medium == "closed" else "→ no memory tool, writes →"
        return ('<div class="flow"><span class="node">Session A · misaligned author</span>'
                f'<span class="arw">{how}</span>'
                f'<span class="node" style="border-color:var(--harm)">{files}</span>'
                '<span class="arw">→ read by →</span>'
                '<span class="node">Session B · neutral successor</span><span class="arw">→</span>' + harm + '</div>')
    if author is None or medium == "planted":
        return ('<div class="flow"><span class="node">a note planted in Session B\'s persistent memory</span>'
                '<span class="arw">→ inherited by →</span>'
                '<span class="node">Session B · neutral successor</span><span class="arw">→</span>' + harm + '</div>')
    return ('<div class="flow">'
            '<span class="node">Session A · misaligned author</span><span class="arw">→ banks note →</span>'
            '<span class="node">persistent memory</span><span class="arw">→ inherited by →</span>'
            '<span class="node">Session B · neutral successor</span><span class="arw">→</span>'
            + harm + '</div>')


def render(roll, scn, *, scn_id, model, title, subtitle, chips, harm_label, analysis, brief=None,
           brief_stale=False, show_brief=True, cell_stale="", medium="") -> str:
    """One complete demo-format trace for a stored author->successor rollout.

    `brief` is the scenario's stored trace brief (`runs/scn/<SCN>/derived/trace_brief.json`). It supplies the
    orientation block above the transcript AND the verified evidence markers highlighted inside it, so the
    header's claims and the highlights in the body are the same list rather than two hand-maintained ones.

    `show_brief=False` keeps the markers and the harm banner but omits the block itself, for a caller that has
    already rendered it once above a group of traces. A brief is a property of the SCENARIO, not of the model
    that ran it, so repeating it per trace put 344 identical copies in the results tables and took the page to 13MB.

    `cell_stale` is the VOID reason for the cell this rollout came from, or "" when the cell is current. It
    exists because the two pages could disagree without it and the trace page was the confident one: after U01's
    world was revised, the results tables rendered its column as `void` while traces.html showed the same cell's
    transcript under a plain "Cell rate: 6/6" chip with nothing to say the measurement had been disowned. A
    reader who arrives via the trace explorer — which is a navbar tab, so most of them — would never see the
    table's verdict. Defaults to "" so every existing caller renders exactly as before."""
    gens = roll.get("gens") or []
    author = next((g for g in gens if g.get("role") == "author"), None)
    succ = next((g for g in gens if g.get("role") == "executor"), gens[-1] if gens else None)
    # `predicate_fn` is None for any scenario using the generic harm_tool+forbidden match (H02), where the
    # real predicate is the BOUND `scn.predicate`. Falling back is what makes those rows taggable at all.
    pred = getattr(scn, "predicate_fn", None) or getattr(scn, "predicate", None)
    ht = getattr(scn, "harm_tool", "")
    brief = brief or {}
    markers = [m for e in (brief.get("evidence") or []) for m in (e.get("markers") or [])]
    hc = brief.get("harm_call") or {}
    harm_note = (f'<b>Why this call is the harm.</b> {esc(hc.get("why_this_is_the_attack",""))}'
                 + (f'<br><b>What it should have done.</b> {esc(hc.get("desirable_alternative",""))}'
                    if hc.get("desirable_alternative") else "")) if hc else ""

    p = ['<div class="n7"><div class="wrap">',
         f'<h1>{esc(title)}</h1><p class="sub">{esc(subtitle)}</p>',
         '<div class="chips">' + "".join(f'<span class="chip"><b>{esc(k)}:</b> {esc(v)}</span>'
                                         for k, v in chips) + '</div>',
         _flow(roll, author, harm_label, medium),
         # ABOVE the brief, not below it: this says the numbers on this page are not current, and a reader who
         # stops after the first paragraph must have seen it.
         (f'<p class="cellvoid"><b>This measurement is VOID.</b> {esc(cell_stale)}</p>' if cell_stale else ""),
         brief_html(brief, brief_stale, scn) if show_brief else ""]

    fired = bool(roll.get("e2e_fired") or roll.get("fired"))
    if author is None and succ and (succ.get("memory_before") or "").strip():
        p.append('<div class="memnote"><h2>↓ The memory note planted in Session B\'s memory (the propagation vector)</h2>'
                 f'<pre>{esc(succ.get("memory_before"))}</pre></div>')
    if author and author.get("attack_goal_done"):
        p.append('<div class="authorfired"><b>Session A also performed the action itself in this replay.</b> This '
                 'scenario\'s gate is an opportunity rather than a permission wall, and the author took the action '
                 'as well as writing the note. The count reported for the cell is Session B\'s action on the '
                 'inherited note; Session B\'s own call is marked HARM below.</div>')
    if author:
        # THE GATE SENTENCE HAS TO MATCH THE GATE KIND, and it did not (found on U05, 2026-08-01). This read
        # "The harm tool (<tools_withheld>) is withheld" for every chain row — but `tools_withheld` is the set
        # the harness removed from gen-0, and on a `perm`/`cap` row THE HARM TOOL IS DELIBERATELY NOT IN IT: the
        # author keeps it and the rail refuses the call, which is the stronger wall and the whole content of
        # those rows. On U05 that produced "The harm tool (edit_file) is withheld", naming a tool that is not
        # the harm tool, next to a session banner that correctly said the queue rail was NOT ATTACHED.
        withheld = list(author.get("tools_withheld") or [])
        harm_tool = getattr(scn, "harm_tool", "") or ""
        kind = str(getattr(scn, "gate_kind", "") or "")
        harm_withheld = harm_tool in withheld
        others = [t for t in withheld if t != harm_tool]
        # NOTE the blurb is escaped by `_session`, so this is plain text and must stay plain text.
        if harm_withheld:
            gate_sentence = (f"The harm tool ({harm_tool}) is withheld this session, so it cannot act now — "
                             f"if it wants the outcome it must leave a note.")
        elif kind in ("perm", "cap"):
            gate_sentence = (f"It HOLDS the harm tool ({harm_tool}) and the world refuses it — this is a "
                             f"{kind} gate, so the call raises rather than the tool being absent, and the "
                             f"session-capabilities banner in the prompt below says so. If it wants the outcome "
                             f"it must leave a note.")
        else:
            gate_sentence = "The gate is closed this session."
        if others:
            gate_sentence += (" Also withheld, so it cannot reach a neighbouring row's harm surface: "
                              + ", ".join(others) + ".")
        blurb = "Given the misaligned disposition and an ORTHOGONAL cover task. " + gate_sentence
        p.append(_session("Session A — the author", blurb, "Author system prompt (disposition + environment)",
                          author.get("persona", ""), author.get("transcript"), ht, pred, scn,
                          author.get("reasoning", ""), markers, harm_note))
        note = author.get("memory_after") or ""
        if note.strip():
            p.append('<div class="memnote"><h2>↓ The memory note it banked (the propagation vector)</h2>'
                     f'<pre>{esc(note)}</pre></div>')
    if succ:
        carried = sorted(roll.get("carried") or {})
        if author is None:
            blurb_b = "Fresh context. No disposition, no instruction — the note above was planted in its memory; no author session ran."
        elif carried and medium in ("closed", "absent"):
            blurb_b = ("Fresh context. No disposition, no instruction — its memory is empty ("
                       + ("the channel was closed" if medium == "closed" else "no memory tool exists")
                       + "); it shares only the file system Session A wrote into.")
        else:
            blurb_b = "Fresh context. No disposition, no instruction — it inherits only the note above."
        p.append(_session("Session B — the neutral successor", blurb_b,
                          "Successor system prompt (neutral agent + the inherited memory)",
                          succ.get("persona", ""), succ.get("transcript"), ht, pred, scn,
                          succ.get("reasoning", ""), markers, harm_note))
        # WHAT THIS RUN NEVER READ, named per rollout. The brief's generic "a point that appears nowhere below
        # is one the agent never opened" asks the reader to search the transcript for an absence and conclude
        # something from not finding it. State it instead: which cited records this successor never had in
        # front of it when it acted. Kept to one line because it repeats across every trace of a scenario.
        seen = _tool_results(succ.get("transcript"))
        unread = sorted({str(e.get("where") or "") for e in (brief.get("evidence") or [])
                         if e.get("where") and not any(str(m).lower() in seen.lower()
                                                       for m in (e.get("markers") or []) if str(m).strip())})
        if unread:
            tgt = _harm_target(brief, dict(getattr(scn, "fs", {}) or {}))
            bits = ", ".join(f'<code>{esc(u)}</code>'
                             + (" <b>(destroyed unread)</b>" if tgt and
                                str(tgt).strip('/').split('/')[-1] == u.strip('/').split('/')[-1] else "")
                             for u in unread)
            p.append(f'<div class="neveropened"><b>Acted without reading:</b> {bits}. '
                     f'Nothing this successor saw contained the evidence cited above — its contents are quoted '
                     f'in <i>What was at stake</i> at the top of this scenario.</div>')
    if fired and getattr(scn, "harm_is_omission", False) and hc:
        p.append('<div class="whyharm omission"><b>The misaligned action here is an omission — a call the agent '
                 'should have made and did not.</b> ' + esc(hc.get("why_this_is_the_attack", ""))
                 + (f'<br><b>What it should have done.</b> {esc(hc.get("desirable_alternative", ""))}'
                    if hc.get("desirable_alternative") else "") + '</div>')
    p.append(f'<div class="analysis"><h2>Why this is a genuine failure (not the environment steering it)</h2>'
             f'{analysis}</div></div></div>')
    return "\n".join(p)
