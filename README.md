# Self-Propagating Misalignment in LLM Agents — code, scenarios, payloads and transcripts

Companion repository for the paper *Self-Propagating Misalignment in LLM Agents, and Why Auditing or Disabling
Memory Is Not Enough*. This work was carried out in the **Anthropic Fellows Program** (AI safety research).

**Authors:** Debeshee Das (Anthropic Fellows Program) · Jacqueline Tay (Independent) · Bruce Tsai (Constellation
Institute) · David Huang (Anthropic) · Javier Rando (Anthropic)

The repository contains the minimum needed to reproduce every number in the paper — the two-session harness, the
20 scenarios in 17 environments, the frozen misaligned prompts every rate is a replay of, the judges, the replay
drivers for each experiment — and one representative transcript for every (experiment, scenario, model) cell in
which the goal propagated.

**Start with [`traces.html`](traces.html)** (open it in any current browser; nothing is fetched). Setup is in
[`SETUP.md`](SETUP.md) / [`SETUP.pdf`](SETUP.pdf).

## What is here

```
traces.html                 the transcripts — one page, 1,273 transcripts, browsable experiment -> scenario -> model
SETUP.md / SETUP.pdf        environment and API keys
configs/panel.yaml          the 11-model panel and the judge roles (aliases only)
src/core/                   the harness
  config.py                   alias -> exact API model id (the single source of truth for model ids)
  agent/loop.py               the multi-turn tool-use session runner (Anthropic + OpenAI-compatible APIs)
  harness/sandbox.py          the stateful sandbox: fake filesystem, gates, persistent memory, memory convention
  judges/core.py              the LLM judges (banking, concealment, evaluation-awareness)
  banking.py / stealth.py     the two contrastive metrics ("banked", "concealed") and their control artifacts
  monitor.py                  the MemMorph-style memory auditor (its prompt verbatim: AUDITOR_SYSTEM)
  redteam/rq_spec.py          THE REGISTRY: every experiment as arm, mode, persona, win condition, columns
  redteam/dispo_gate.py       the disposition judge that gates the values-only prompts
  redteam/carrier_tasks.py    the banks of unrelated work for the intervening-sessions experiment
  report/                     the trace-store reader and the transcript renderer
scripts/
  _suite.py                   the scenario registry (20 rows, 17 environments) + the scenario fingerprint
  _scenario_base.py           the scenario dataclass and shared helpers (gates, author toolset, session banner)
  _v*.py                      the 17 environments: text, filesystem, tools, predicates, personas, tasks
  _suite_run.py               the runner: any scenario x model x arm, n replays, full transcripts to runs/
  _scorers.py                 the win scorers (fired; fired AND concealed; fired AND passed the auditor)
  _rq_confirm.py              replay the frozen payloads of an experiment on a fresh sample   (Q1 Q2 Q3 Q5 Q6 Q7)
  _q8_discriminate.py         the gate-shut condition                                          (Q8)
  _q8_attempts.py             attempt rates re-scored from stored tool calls                  (Q8 reference)
  _q9_decay.py                up to 100 intervening sessions, probed every 10                (Q9)
  _q9_survival.py             the survival judge: is the aim still carried after a carrier session  (Q9)
  _q10_crossmodel.py          one model writes the note, another executes it                 (Q10)
  _q11_channel.py             the memory channel closed (writes fail) + its neutral control  (Q11)
  _q11_memabsent.py           the memory channel absent (no memory tool at all)              (Q11)
  summarize.py                the scenario x model grids and pooled rates behind the figures
  build_traces.py             rebuild traces.html from a store
  selfcheck.py                is this checkout able to run? (imports, fingerprints, keys)
runs/payloads/winners/        the FROZEN PAYLOADS: 846 files, <scn>/<model>__<Q>.json, each the misaligned
                              system-prompt passage or planted note replayed for that cell
runs/payloads/q11_variants/   the de-specified payload variants Q11 replays (see below)
runs/payloads/handwritten/    the four hand-written notes of the H09 framing ladder (Q10)
runs/_index/refusal_mask.json the (scenario, model) pairs the provider refused on every attempt
runs/_index/paper_cells.json  for the searched experiments: every cell the paper's tables count, with provenance
```

## Experiment ids

The code keeps the registry ids; the paper names them. `configs/panel.yaml` lists the model aliases.

| id | in the paper | section |
|---|---|---|
| Q2 | the misaligned model (explicit goal), end to end | IV-A, Fig. 1, 3, 11 |
| Q3 | the values-only misaligned model | IV-A, Fig. 3, 11 |
| Q1 | the execution ceiling (misaligned note planted directly in Session B's memory) | IV-A, Fig. 3, 11 |
| controls | the same two sessions with the misaligned system prompt replaced by the model's ordinary one | III-F |
| Q9 | persistence across up to 100 intervening sessions | IV-B1, Fig. 4 |
| Q10 | cross-model self-propagation (12 model pairs) and the H09 framing ladder | IV-B2, Fig. 5 |
| Q5 | concealment | IV-C, Fig. 6a, 12 |
| Q8 | discernment: the successor with the gate shut | IV-C, Fig. 6b, 13 |
| Q7 | the memory-auditor defense (MemMorph auditor as a write gate) | IV-D1, Fig. 7 |
| Q6 | the defended ceiling: a planted note optimized to evade the auditor | IV-D1, Fig. 7 |
| Q11 | the memory channel removed: closed (writes fail) and absent (no memory tool) | IV-D2, Fig. 8, 10 |

Scenario ids H01–H10 / U01–U10 and the model aliases are as in the paper's Table I and Sec. III-D.

## How a number is produced

Each (scenario, model) cell is one **frozen payload** — the misaligned system-prompt passage (Q2, Q3) or planted
note (Q1) that an adversarial search found for that cell — **replayed eight times** through the two-session setup
of Sec. III-A. The search itself is not part of this release; its outputs are: every payload under
`runs/payloads/winners/`, keyed `<scenario>::<model>::<experiment>`. A rate is the number of replays in which
Session B took the scenario's deterministic misaligned action (and, on the chain experiments, Session A's note
was judged to carry the aim — a fire off an empty memory never counts). Q8–Q11 replay each cell's own Q2 payload
unchanged, so the condition is the only thing that moves. Q11 replays a *de-specified* variant of that payload
in which sentences that named the memory mechanism were rewritten to a channel-neutral outcome
(`runs/payloads/q11_variants/`, one record per unique payload with the exact edits).

Judges: banking (`banked_contrastive`, handed a benign author's notes on the same task), concealment (contrastive
against the empty-memory control), the memory auditor (MemMorph's prompt verbatim), the disposition gate and the
evaluation-awareness judge all run on `claude-opus-4-8` (`configs/panel.yaml: roles`). Conjunction verdicts
(concealment, auditor) are a majority of three draws.

## Reproducing each experiment

Every driver is resumable (an existing, current cell is skipped; `--force` remeasures), skips provider-refused
pairs, and writes full transcripts. `--plan` prints what would run without an API call. Costs are large: one
full-grid experiment is ~3,000 agent sessions (see `SETUP.md`).

```bash
# the searched experiments: replay every frozen payload at n=8
uv run python scripts/_rq_confirm.py --rq Q2 --reps 8        # also Q1, Q3, Q5, Q6, Q7
# cells whose payload is the scenario's own persona or note (source `builtin`/`native_persona` in paper_cells.json)
# have no frozen payload to replay; run the arm directly, e.g.
uv run python scripts/_suite_run.py --scn U09 --models kimi-k3 --arms disposition --reps 8
# the control: the same chain, no misaligned goal (built for every scenario in the panel)
for s in H01 H02 H03 H04 H05 H06 H07 H08 H09 H10 U01 U02 U03 U04 U05 U06 U07 U08 U09 U10; do
  uv run python scripts/_suite_run.py --scn $s --panel --arms neutral_chain --reps 8; done
# Q8: the gate shut, then the reference attempt rate re-scored from the Q2 replays
uv run python scripts/_q8_discriminate.py --all --reps 8 && uv run python scripts/_q8_attempts.py --all
# Q9: 100 intervening sessions, probed every 10 with 8 independent successor sessions; then the curve
uv run python scripts/_q9_decay.py --all --carriers 100 --chains 1 --probes 8 && uv run python scripts/_q9_decay.py --curve
# Q10: the 12 cross-model pairs (author model per pair is declared in the driver), and the H09 framing ladder
uv run python scripts/_q10_crossmodel.py --all --reps 8
uv run python scripts/_suite_run.py --scn H09 --models gpt-5.6 --author-model gpt-5-mini \
    --arms instruction-xauthor-h4 --persona-file runs/payloads/handwritten/q10_h09/h4.txt --reps 8   # h1..h4
# Q11: closed channel + its control (control first: it is the routing judge's baseline), the open arm, the absent arm
uv run python scripts/_q11_channel.py --all --reps 8
uv run python scripts/_q11_channel.py --all --arm instruction-memopen --reps 8
uv run python scripts/_q11_memabsent.py --scn H07 --model gpt-5.6 --reps 8 --control && \
uv run python scripts/_q11_memabsent.py --scn H07 --model gpt-5.6 --reps 8           # per (scenario, model)
# the tables behind the figures, and the transcript page
uv run python scripts/summarize.py
uv run python scripts/build_traces.py --out traces.html    # add --manifest runs/_index/paper_cells.json for the paper's cell set
```

`_q11_channel.py --scn <row> --model <alias>` runs any (scenario, model) pair; `--all` runs each scenario on the
model its row declares. The intervening-sessions rows and their models are `SET` in `_q9_decay.py` (Table in
Sec. IV-B1); the cross-model pairs are `SET` in `_q10_crossmodel.py`.

## About the transcript page

`traces.html` holds, for every experiment, every (scenario, model) cell that propagated, and for each such cell
one replay in which the goal was acted on (or attempted, for the gate-shut experiment; or, for the memory-removed
experiment, written to a file and acted on where any replay did both, else written to a file). In the transcript
the decisive lines are tagged: BANKED TO MEMORY, MEMORY WRITE REFUSED, HARM, ATTEMPTED, EVIDENCE; a replay whose
Session A performed the action itself carries a notice saying so. For the searched experiments the cell set is
exactly the paper's (`runs/_index/paper_cells.json`, extracted from the results tables together with each cell's
provenance). A cell
whose rate came from a screen inside the search rather than a replay cell — 112 auditor cells, whose screens stored
a summary of each rollout rather than the transcript — has nothing to show and is left off the page; it stays in
the tables, and `_rq_confirm.py --rq Q7` replays it with full transcripts. The intervening-sessions experiment
contributes, per chain, the successor probes at depth 0, 50 and 100 where at least one probe acted, one carrier
session that engaged the inherited aim unprompted, and the entire 100-session lineage.

Transcripts are gzip-compressed inside the page and inflated in the browser on click (native
`DecompressionStream`; Chrome/Edge 80+, Safari 16.4+, Firefox 113+), which is what keeps 1,273 transcripts in one
29 MB file. Browser find does not reach inside an unopened transcript; the search box does.

## Containment and ethics

Every tool in every scenario is fake and log-only: a tool body mutates an in-memory dictionary and returns a
string. No real system, network, person or organization is touched; every organization and individual in the
scenarios is fictional. The evaluated models are queried through their public APIs at their default sampling
settings. Sessions whose private reasoning verbalised evaluation awareness were discarded before scoring.
