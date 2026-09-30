#!/usr/bin/env python3
"""SELF-CHECK — is this checkout able to run the experiments? No agent session is started.

    uv run python scripts/selfcheck.py            # imports, registry, fingerprints, payloads, which keys are set
    uv run python scripts/selfcheck.py --ping     # additionally one 1-token call per provider that has a key

Prints names only — never a key value.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path[:0] = ["src", "scripts"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ping", action="store_true", help="one 1-token call per provider whose key is set")
    a = ap.parse_args()
    ok = True

    print("== imports")
    import _suite as S                                             # noqa: E402
    from core.config import _REGISTRY, available_models            # noqa: E402
    from core.redteam import rq_spec as RQ                         # noqa: E402
    import importlib
    for m in ("_suite_run", "_rq_confirm", "_q8_attempts", "_q8_discriminate", "_q9_decay", "_q9_survival",
              "_q10_crossmodel", "_q11_channel", "_q11_memabsent", "build_traces", "summarize"):
        importlib.import_module(m)
    print("   ok")

    print("== scenario registry")
    rows = S._suite_rows()
    probs = S.validate()
    print(f"   {len(rows)} scenarios in {len({m['env'] for m in rows.values()})} environments")
    for sid in sorted(rows):
        print(f"   {sid} {S.scenario_fingerprint(S.load(sid))}  {rows[sid]['env']:4s} harm {rows[sid]['harm']}")
    if probs:
        print("   registry problems:", probs)
    viol = RQ.check_single_factor()
    print("   single-factor violations:", viol or "none")
    ok &= not viol

    print("== frozen payloads")
    n = {}
    for p in glob.glob("runs/payloads/winners/*/*.json"):
        d = json.load(open(p)); n[d["rq"]] = n.get(d["rq"], 0) + 1
    print("   winners per experiment:", dict(sorted(n.items())))
    print("   Q11 payload variants:", len(glob.glob("runs/payloads/q11_variants/by_payload/*.json")))

    print("== API keys (names only)")
    av = available_models()
    by_key = {}
    for spec in _REGISTRY.values():
        by_key.setdefault(spec.api_key_env, []).append(spec.alias)
    for env, aliases in by_key.items():
        present = bool(os.environ.get(env)) or (env == "CLAUDE_API_KEY" and bool(os.environ.get("ANTHROPIC_API_KEY")))
        print(f"   {env:20s} {'set  ' if present else 'UNSET'}  -> {', '.join(aliases)}")
    if not av.get("opus"):
        print("   !! the judge model (`opus`, CLAUDE_API_KEY) has no key: no experiment can be scored")
        ok = False

    if a.ping:
        print("== ping (one 1-token call per provider)")
        from core.models import get_client
        seen = set()
        for spec in _REGISTRY.values():
            if spec.provider in seen or not av.get(spec.alias):
                continue
            seen.add(spec.provider)
            try:
                r = get_client(spec.alias).chat(messages=[{"role": "user", "content": "hi"}], max_tokens=1)
                print(f"   {spec.provider:11s} via {spec.alias:16s} ok ({r.usage.get('input_tokens', '?')} in)")
            except Exception as e:
                print(f"   {spec.provider:11s} via {spec.alias:16s} FAILED: {type(e).__name__}: {str(e)[:120]}")
                ok = False
    print("== " + ("ready" if ok else "NOT READY — see above"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
