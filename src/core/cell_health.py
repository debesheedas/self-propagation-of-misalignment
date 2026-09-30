"""How trustworthy is a stored cell's denominator?

WHY THIS EXISTS (2026-08-07). A cell reports `fired=0/1` and says nothing about the two reps that died on a
provider error. Over one day of H06 work that ambiguity produced the same mistake repeatedly: `0/0` read as a
clean control when nothing had been measured, `0/5` read as a null when five of twelve sessions had hit the
turn ceiling, `0/1` read as a result when it was one surviving rep out of three. Each time the truth was one
field away in `reps[i]['error']`, and each time it had to be dug out by hand.

The retry policy in `core/models/base.py` is correct and deliberately bounded (one layer, error-aware, wall
clock capped — it was built after two retry layers multiplied into 2.3-hour silent stalls). It cannot stop a
provider outage that outlasts its budget. What it can't do, this can: make the damage impossible to miss.

    from core.cell_health import health, verdict_line
    h = health(cell_dict)
    if not h.trustworthy: ...        # denominator too thin or too degraded to read

NOT a scorer and not wired into the pipeline: a reporting aid, safe to call anywhere.
"""
from __future__ import annotations
from dataclasses import dataclass, field

# A session that ended this way did not finish its work; its rep's contribution is doubtful.
DEGRADED_STOPS = {"max_turns", "api_error", "length", "refusal", "", None}


@dataclass
class Health:
    n_reps: int = 0                      # reps stored
    n_scored: int = 0                    # reps that reached the denominator
    n_errored: int = 0                   # reps carrying an error
    n_sessions: int = 0
    n_degraded: int = 0                  # sessions that did not end cleanly
    errors: dict = field(default_factory=dict)
    stops: dict = field(default_factory=dict)

    @property
    def lost(self) -> int:
        return max(0, self.n_reps - self.n_scored)

    @property
    def lost_frac(self) -> float:
        """Share of REQUESTED reps that never reached the denominator."""
        return (self.lost / self.n_reps) if self.n_reps else 0.0

    @property
    def trustworthy(self) -> bool:
        """A denominator worth reading: >=3 scored reps, under a third of REPS lost, under a third of sessions
        degraded.

        THE LOST-REP TEST WAS MISSING AND THAT IS THE CASE IT EXISTS FOR (2026-08-09). The rule was "3 scored
        reps AND <=34% of SESSIONS degraded", and both are computed on the wrong denominator for the failure
        that actually happens. Measured on U08/Q5's confirmation: 6 reps requested, THREE lost to
        `provider_refusal`, leaving 2/3 — and it read `trustworthy: true`. It passed the first test on exactly 3
        scored reps and the second because a chain rep is TWO sessions, so 3 dead author generations are
        3/12 = 25% of sessions rather than 3/6 = 50% of reps. Halving the visible damage is precisely what a
        chain row does to this ratio, so the check was weakest on the rows it matters most for.
        A cell that lost half its reps must not read as a clean denominator whatever the session arithmetic says.
        """
        if self.n_scored < 3:
            return False
        if self.lost_frac > 0.34:
            return False
        return self.n_sessions == 0 or (self.n_degraded / self.n_sessions) <= 0.34

    @property
    def why(self) -> str:
        if self.n_scored == 0:
            return "NOTHING MEASURED - every rep was excluded; this is not a result, clean or otherwise"
        if self.n_scored < 3:
            return f"only {self.n_scored} scored rep(s) of {self.n_reps} - too thin to read"
        if self.lost_frac > 0.34:
            return (f"{self.lost}/{self.n_reps} REPS were lost ({', '.join(self.errors) or 'errored'}) - "
                    f"the rate is over {self.n_scored} surviving rep(s), not {self.n_reps}")
        if self.n_sessions and self.n_degraded / self.n_sessions > 0.34:
            return (f"{self.n_degraded}/{self.n_sessions} sessions ended degraded "
                    f"({', '.join(k for k in self.stops if k in DEGRADED_STOPS or k is None)}) - "
                    "the zeros may be the harness, not the model")
        return "ok"


def health(cell: dict) -> Health:
    """Summarise a stored cell dict (the JSON at runs/<Q>/<SCN>/<model>/<arm>.json)."""
    h = Health()
    reps = (cell or {}).get("reps") or []
    h.n_reps = len(reps)
    for r in reps:
        err = r.get("error")
        if err:
            h.n_errored += 1
            k = str(err).split(":")[0][:40]
            h.errors[k] = h.errors.get(k, 0) + 1
        else:
            h.n_scored += 1
        for g in (r.get("gens") or []):
            h.n_sessions += 1
            st = g.get("stop")
            h.stops[st] = h.stops.get(st, 0) + 1
            if st in DEGRADED_STOPS:
                h.n_degraded += 1
    return h


def verdict_line(cell: dict, label: str = "") -> str:
    """One line fit to print beside a rate. Never lets a degraded denominator pass as a result."""
    h = health(cell)
    rate = str((cell or {}).get("fired"))
    flag = "" if h.trustworthy else f"   <-- {h.why}"
    lost = f" (lost {h.lost})" if h.lost else ""
    return f"{label:24} fired={rate:7} scored={h.n_scored}/{h.n_reps}{lost}{flag}"
