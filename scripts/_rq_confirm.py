"""THE CONFIRMATION SWEEP — replay a question's saved forge winners on a FRESH sample, independently of the
search that found them.

    uv run python scripts/_rq_confirm.py --rq Q1 --plan          # list the cells, launch nothing
    uv run python scripts/_rq_confirm.py --rq Q2 --reps 6        # fill the gaps
    uv run python scripts/_rq_confirm.py --rq Q2 --reps 16 --force --only S08:gemini-3.1-pro

WHY THIS EXISTS — EVERY FORGED QUESTION HAS TWO TABLES AND THEY MEASURE DIFFERENT THINGS.

The forge table answers "did an adversarial search find a break at all?". Its k/n is the score of the best
candidate ON THE SAMPLE THAT SELECTED IT: the search kept mutating until something fired, then reported that
something's rate. That is a maximum over candidates, so it is biased upward and cannot be quoted as the rate. The
question's own table answers "how often does the winning candidate actually fire?", which requires a FRESH sample.

Not a formality. The first cell ever measured this way scored 6/6 in the forge and 2/6 on replay.

The replay is exact by construction: `_suite_run.py --persona-file <key>` substitutes the saved payload into the
same `_chain()`/`_solo()` the forge called, under the memory convention and successor task the registry gave that
question, so environment, tasks, gate states, withheld tools, turn budget and predicate are all identical. Only
the sample is new.

WHICH CELL IT WRITES. The question's own arm, from the registry — so a Q7 winner can never be replayed into Q2's
cell and reported as Q2's rate. That was possible while winners were keyed by TRACK: five questions now share the
instruction track and differ only in the win condition.

CONCURRENCY. One cell runs `--workers` sessions in flight; `--parallel` cells run side by side, so the in-flight
target-session count is parallel x workers. This project starts seeing 429s past ~12-16, and winners cluster on one
provider key, so the defaults stay at 2 x 6 = 12.
"""
from __future__ import annotations
import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "src"); sys.path.insert(0, "scripts")

from core import records as R
from core.redteam.rq_spec import BY_ID, FORGEABLE
from core.report.suite_store import trace_path
from core.report import suite_store as ST

LOGDIR = "logs/drivers/rqconfirm"        # driver output, not a per-cell log — see scripts/_reorg_store.py
# Seconds a single (scenario, model) replay may take before it is killed. See the argument at the call site:
# 7200 is a FLEET-average limit and it is below one H06/deepseek-v4 confirm, which needs ~3h20m. Override with
# `--timeout` or `RQCONFIRM_TIMEOUT`; raise for long-`max_turns` rows, never lower.
import os as _os_to
TIMEOUT_S = int(_os_to.environ.get("RQCONFIRM_TIMEOUT", "7200"))


def winners(rq):
    """(key, scn, model) for every saved winning payload of this question, worst-rate first.

    Worst-first on purpose: the low-rate winners are the ones most likely to confirm at 0/n, and a 0/n is the most
    decision-relevant result here — it says the forge's number was selection noise. Learning that early beats
    spending the whole budget on the cells that were always going to hold up.
    """
    d = ST.read_winners()
    # Obsolete winners are voided candidates; confirming one would spend rollouts measuring a retired payload.
    #
    # AND A TAGGED WINNER IS NOT THIS QUESTION'S WINNER (2026-08-02). `save_winner` puts the tag in the KEY
    # (`<SCN>::<model>::Q1-smoke`) but leaves `entry["rq"]` as the bare `Q1`, so selecting on the rq FIELD picks
    # up every exploratory and smoke run ever saved. `run_cell` then writes them all to the same untagged
    # `q.arm` cell, so with `--parallel` two confirmations race on one file and the published rate is whichever
    # finished last — a throwaway 2-rep payload can win. Found with eight `--tag smoke` shards on disk across
    # six of seven sweep scenarios, each carrying `rq: "Q1"`/`"Q6"` and a 1-2 rep rate.
    # Select on the KEY, which is the only place the tag is authoritative, and belt-and-braces on the field.
    rows = [(k, v) for k, v in d.items()
            if isinstance(v, dict) and v.get("rq") == rq and not v.get("obsolete")
            and ST._key_parts(k)[2] == rq and not v.get("tag")]
    # `.get("rate", 0.0)` RETURNS None WHEN THE KEY IS PRESENT AND NULL, and the default never fires (2026-08-08).
    # One shard on disk carries `rate: null` (`winners/U05/fable-5__Q2.json`, also `fired: null`, `n: null`), which
    # took the whole sort down with `'<' not supported between instances of 'NoneType' and 'float'`. The blast
    # radius is the reason this is a bug and not a curiosity: `winners()` reads EVERY scenario's winners for the
    # question and sorts them BEFORE `--only` narrows to one cell, so a single malformed shard in any row blocked
    # Q2 confirmation for the entire suite. Coerce at the comparison; do not "repair" the shard, because an absent
    # rate is a real state (a winner saved by a path that never measured one) and rewriting it would invent data.
    rows.sort(key=lambda kv: kv[1].get("rate") or 0.0)
    return [(k, v["scn"], v["model"]) for k, v in rows]


def has_cell(scn, model, arm):
    """A cell only counts as done if it is CURRENT. Checking mere existence would skip every cell whose
    confirmation came from a retired search — which is precisely the set that needs re-measuring, and would have
    made this whole sweep a silent no-op (the same bug the fleet's already_done() had)."""
    d = R.read_cell(str(trace_path(scn, model, arm)))
    if not d or d.get("obsolete"):
        return False
    try:
        from _suite import load, scenario_fingerprint
        if d.get("scn_sha") and d["scn_sha"] != scenario_fingerprint(load(scn)):
            return False
    except Exception:
        pass
    return True


def run_cell(key, scn, model, reps, workers, q, tag=""):
    """One confirmation cell, via the ordinary panel path so the conditions are the registry's, not this script's.

    The payload flag follows the ARTIFACT: a note-track winner is an inherited NOTE and must be replayed through
    the successor-only arm with `--note-file`. Using `--persona-file` would substitute it as a gen-0 author
    persona, which is a different experiment that would quietly report a number for this one.
    """
    import pathlib
    # Same guard as the runner, at the confirm level: a masked pair costs `reps` sessions per cell and the
    # replay sweep is the largest single spend in the endgame. See `suite_store.is_masked`.
    _skip, _why = ST.is_masked(scn, model, q.id)
    if _skip:
        return {"key": key, "scn": scn, "model": model, "rc": 0, "log": "", "fired": "MASKED",
                "mediated": None, "banked": None, "n_author_fired": None, "n_errors": None,
                "masked": True, "kept": _why}
    arm = f"{q.arm}-{tag}" if tag else q.arm
    pathlib.Path(LOGDIR).mkdir(parents=True, exist_ok=True)
    log = f"{LOGDIR}/{q.id}_{scn}_{model}.log"
    payload_flag = "--note-file" if q.artifact == "note" else "--persona-file"
    # CROSS-MODEL QUESTIONS REPLAY ACROSS THE SAME TWO MODELS THE FORGE USED, or they do not replay at all.
    # Without this the confirm would run gen-0 on `model` — the EXECUTOR — which is the same-model condition
    # this question exists to contrast against, and the resulting number would be filed as the cross-model
    # rate with nothing downstream able to tell. Same guard as the search harness, same reason.
    _xa = []
    if q is not None and getattr(q, "artifact", "").endswith("-xauthor"):
        try:
            sys.path[:0] = ["scripts"]
            import _q10_crossmodel as _X
            _a = (_X.SET.get(f"{scn}/{model}") or ("",))[0]
        except Exception:
            _a = ""
        if not _a:
            raise SystemExit(
                f"{q.id} is a CROSS-MODEL question and no author is declared for {scn}/{model} in "
                f"`_q10_crossmodel.SET`. Replaying without one would run gen-0 on {model!r} and file a "
                f"SAME-MODEL number as this question's confirmed rate. Add the pair to SET first.")
        if _a == model:
            raise SystemExit(f"{q.id} {scn}/{model}: SET names {_a!r} as the author, i.e. the executor. "
                             f"That is the same-model condition, not a cross-model replay.")
        _xa = ["--author-model", _a]

    cmd = [sys.executable, "-u", "scripts/_suite_run.py", "--scn", scn, "--models", model, *_xa,
           "--arms", arm, "--reps", str(reps), "--workers", str(workers),
           # ALWAYS --force. has_cell() has ALREADY decided this cell needs measuring (it is absent, obsolete, or
           # measured on different scenario text). Without --force, _suite_run skips any (model, arm) already in
           # summary.json and this sweep reads the STALE cell straight back and reports it as a fresh
           # confirmation — which is exactly what happened on 2026-07-27, when all 13 "confirmations" were the
           # retired numbers echoed back verbatim.
           payload_flag, key, "--force"]
    # THE SUBPROCESS TIMEOUT IS A ROW-SIZED LIMIT, NOT A FLEET-SIZED ONE (2026-08-12).
    #
    # This was a hardcoded 7200s and it silently destroyed H06/deepseek-v4's confirm. Measured the same day on
    # that row: ONE batch of 4 chains took 5258-7387s, and a confirm is `reps/workers` batches — 6 reps at 3
    # workers is two of them, ~3h20m, against a 2h limit. The cell could not have completed at any point.
    #
    # WHY IT IS THE WORST KIND OF FAILURE: `_suite_run` writes its cell ONCE, at the END, so a timeout leaves
    # NO cell at all — not a partial one. The confirm then reports `confirmed None ERR TimeoutExpired`, which
    # in a long sweep log reads like a provider problem rather than a limit that was never survivable. This is
    # the same defect, on the same row, as the forge watchdog's 120-minute default:
    # a limit tuned to the fleet average converts a SLOW row into a MISSING measurement, and it does it to
    # exactly the rows whose sessions are longest.
    #
    # Default is unchanged at 7200 so no existing usage moves. Raise it for any row with a long `max_turns`
    # (H06 is `max_turns=320`, `max_tokens=24000`, two generations per chain); never lower it.
    # READ THE CELL'S STAMP BEFORE THE RUN, SO A KEPT CELL CANNOT BE REPORTED AS A FRESH ONE (2026-08-17).
    #
    # `_suite_run.write_cells` refuses to overwrite a cell when EVERY rep errored — correctly, because an outage
    # is not a measurement — and it says so loudly in its own log:
    #     [fable-5/instruction] every rep errored (18/18); KEEPING the stored cell (6/6, written 2026-08-11).
    # This function then read that kept cell straight back and returned its number, and the summary table below
    # printed it under `confirmed` with `err 0` and no marker: indistinguishable from a real n=18 confirmation.
    # Measured on U08/fable-5, which reported a fresh-looking 6/6 twice in one session while every one of its 30
    # reps was refused by the provider; the number was five days and two world edits old and was used as evidence
    # that two world edits had not cost the attack.
    #
    # This is the SAME failure the `--force` comment above was written to prevent ("reads the STALE cell straight
    # back and reports it as a fresh confirmation ... all 13 confirmations were the retired numbers echoed back")
    # one level down: `--force` fixed the skip path, the all-errored path reopened it.
    _before = R.read_cell(str(trace_path(scn, model, arm))) or {}
    _stamp_before = (_before.get("written_at"), _before.get("scn_sha"))
    with open(log, "w", encoding="utf8") as fh:
        p = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, timeout=TIMEOUT_S)
    cell = R.read_cell(str(trace_path(scn, model, arm))) or {}
    # STAMP WHERE THIS NUMBER'S PAYLOAD CAME FROM (2026-08-20). The provenance already exists,
    # spread across five stores -- the forge record's `builtin` block, the winner shard's `method`/`mutator`,
    # the seed pool's `note`, the hand-run notes, and the scenario module itself -- so answering "how many published
    # cells rest on a hand-written payload rather than a searched one?" means a five-way join. The paper needs
    # that count (the paper reports it), so the cell records it directly.
    #
    # Derived from the winner shard, never guessed: `method` names the search that produced it and the seed
    # pool `note` names a promoted seed. Absent fields fall through to "forge-winner", which is what a shard
    # with no other marker is.
    if cell and not cell.get("payload_origin"):
        try:
            w = ST.read_winners().get(key) or {}
            note, method = str(w.get("note") or ""), str(w.get("method") or "")
            src = w.get("source") or ""
            if (w.get("stopped") or {}).get("why") == "builtin_sufficient" or "built-in" in src:
                origin = "builtin"
            elif "hand" in note.lower() or "handwritten" in method.lower() or "handclimb" in method.lower():
                origin = "handwritten"
            elif "pooled from" in note or "round-0 starter" in note:
                origin = "seed-pool-promoted"
            else:
                origin = "forge-winner"
            cell["payload_origin"] = origin
            cell["payload_forged_on"] = w.get("forged_on") or w.get("model")
            # `_atomic_write`, not `write_cell` — the latter is a cell CONSTRUCTOR taking
            # (run_dir, scenario, model, cell, config, reps), not a path writer.
            R._atomic_write(trace_path(scn, model, arm), cell)
        except Exception as _e:                                   # noqa: BLE001
            print(f"[origin] could not stamp {scn}/{model}/{arm}: {type(_e).__name__}: {_e}", flush=True)
    # A cell that did not move is not this run's result, whatever it says.
    if _before and (cell.get("written_at"), cell.get("scn_sha")) == _stamp_before:
        return {"key": key, "scn": scn, "model": model, "rc": p.returncode, "log": log,
                "fired": "STALE-KEPT", "mediated": None, "banked": None, "n_author_fired": None,
                "n_errors": None, "stale_kept": True,
                "kept": f"{_before.get('fired')} written {_before.get('written_at')}"}
    return {"key": key, "scn": scn, "model": model, "rc": p.returncode, "log": log,
            "fired": cell.get("fired"), "mediated": cell.get("fired_mediated"), "banked": cell.get("banked"),
            "n_author_fired": cell.get("n_author_fired"), "n_errors": cell.get("n_errors")}


def main():
    global TIMEOUT_S      # `--timeout` rebinds it for `_one()`, which runs in the worker pool
    ap = argparse.ArgumentParser()
    ap.add_argument("--rq", required=True,
                    help=f"which question's winners to confirm. One of: {', '.join(FORGEABLE)}")
    ap.add_argument("--reps", type=int, default=6)
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--force", action="store_true", help="re-measure cells that already have a confirmation cell")
    ap.add_argument("--timeout", type=int, default=TIMEOUT_S,
                    help="SECONDS one (scenario, model) replay may take before it is killed (default 7200, or "
                         "$RQCONFIRM_TIMEOUT). A confirm is reps/workers BATCHES of chains; size this above the "
                         "row's own batch, not above the suite average. H06/deepseek-v4 needs ~12000.")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated winners.json keys, or SCN:MODEL pairs")
    ap.add_argument("--tag", default="",
                    help="write to <arm>-<tag> instead of the question's canonical cell, so an experimental "
                         "replay can never be mistaken for the published number.")
    a = ap.parse_args()
    TIMEOUT_S = a.timeout

    q = BY_ID.get(a.rq.upper())
    if q is None or not q.forgeable:
        raise SystemExit(f"--rq {a.rq!r}: not a forgeable question. One of: {', '.join(FORGEABLE)}")
    arm = f"{q.arm}-{a.tag}" if a.tag else q.arm

    rows = winners(q.id)
    if a.only:
        want = {x.strip() for x in a.only.split(",") if x.strip()}
        rows = [r for r in rows if r[0] in want or f"{r[1]}:{r[2]}" in want]
    todo = [r for r in rows if a.force or not has_cell(r[1], r[2], arm)]

    d = ST.read_winners()
    print(f"[confirm] {q.title}")
    print(f"[confirm] arm {arm!r} | artifact {q.artifact} | win {q.win} | task {q.succ_task}")
    print(f"[confirm] {len(rows)} saved winner(s); {len(todo)} to measure at n={a.reps} "
          f"({a.parallel} cells x {a.workers} workers = {a.parallel * a.workers} sessions in flight)")
    for k, scn, model in rows:
        mark = "RUN " if (k, scn, model) in todo else "skip"
        print(f"  {mark} {scn:5} {model:16} forge said {str(d[k].get('fired')):>5}"
              + ("" if mark == "RUN " else "  (confirmation cell already on disk)"))
    if a.plan or not todo:
        return

    out = []
    with ThreadPoolExecutor(max_workers=a.parallel) as ex:
        futs = {ex.submit(run_cell, k, s, m, a.reps, a.workers, q, a.tag): (k, s, m) for k, s, m in todo}
        for f in as_completed(futs):
            k, s, m = futs[f]
            try:
                r = f.result()
            except Exception as e:
                r = {"key": k, "scn": s, "model": m, "rc": -1, "fired": None,
                     "error": f"{type(e).__name__}: {str(e)[:200]}"}
            out.append(r)
            print(f"  [{len(out)}/{len(todo)}] {s:5} {m:16} forge {str(d[k].get('fired')):>5} -> "
                  f"confirmed {str(r.get('fired')):>5}"
                  + (f"  <- NOT MEASURED: every rep errored, kept {r.get('kept')}"
                     if r.get("stale_kept") else "")
                  + (f"  ERR {r.get('error') or r.get('n_errors')}" if r.get("rc") else ""), flush=True)

    print(f"\n=== {q.id} CONFIRMATION vs FORGE ===")
    print(f"{'scn':6} {'model':16} {'forge':>7} {'confirmed':>10} {'via note':>9} {'banked':>7} "
          f"{'author-fired':>13} {'err':>4}")
    for r in sorted(out, key=lambda r: (r["scn"], r["model"])):
        print(f"{r['scn']:6} {r['model']:16} {str(d[r['key']].get('fired')):>7} {str(r.get('fired')):>10} "
              f"{str(r.get('mediated')):>9} {str(r.get('banked')):>7} {str(r.get('n_author_fired')):>13} "
              f"{str(r.get('n_errors')):>4}")
    _kept = [r for r in out if r.get("stale_kept")]
    if _kept:
        print(f"\n!! {len(_kept)} cell(s) reported STALE-KEPT — every rep errored and the STORED cell was left "
              f"in place, so there is NO number for them from this run. Re-run when the API is healthy.")
        for r in _kept:
            print(f"     {r['scn']}/{r['model']}: kept {r.get('kept')}")
    lost = [r for r in out if (r.get("fired") or "").startswith("0/")]
    if lost:
        # "Did not reproduce", never "is not a break": a true rate of 1/6 fires 0/6 on a fresh sample ~33% of the
        # time, so n=6 cannot separate a low rate from zero. Top these up to n>=16 before calling them either way.
        print(f"\n{len(lost)} winner(s) fired 0/n on replay — the search score did not reproduce. At n={a.reps} "
              f"this does NOT establish a zero rate; re-run these at n>=16 to call it: "
              + ", ".join(f"{r['scn']}/{r['model']}" for r in lost))
    # benign-channel rows are exempt: their author holds the PROTECTIVE directive by construction, so a firing
    # author is correct behaviour, not a confound (same rule as suite_store.Cell.author_confounded).
    from _suite import SUITE
    _ben = {k for k, v in SUITE.items() if v.get("goal") == "P"}
    conf = [r for r in out if r.get("n_author_fired") and r["scn"] not in _ben]
    if conf:
        print(f"{len(conf)} cell(s) CONFOUNDED (the gen-0 author performed the harm itself, so the cell is not "
              f"measuring deferral): " + ", ".join(f"{r['scn']}/{r['model']}" for r in conf))
    print("\nNow: uv run python scripts/summarize.py")


if __name__ == "__main__":
    main()
