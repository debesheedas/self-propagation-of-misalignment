"""READER-FACING NAMES — the wording used in the paper, in one place, for every page that renders a label.

The registry name in `_suite.SUITE[scn]["name"]` is an internal handle written for whoever built the row. These
name the ACT, in the present tense, for someone who has never seen the study — the wording of Table I of the
paper — together with the misalignment class assigned to each scenario and the display names of the models and
experiments.
"""
from __future__ import annotations

# Table I of the paper: id -> (what the successor does, misalignment class).
NAMES = {
    "H01": ("Copies itself to an unwatched node", "Self-preservation"),
    "H02": ("Unseals the client signing keys", "Power-seeking"),
    "H03": ("Deletes the only off-site backup", "Disclosed unilateral intervention"),
    "H04": ("Hides its own actions from audit", "Undermining oversight"),
    "H05": ("Marks unsafe answers as safe", "Motivated mislabeling"),
    "H06": ("Escapes via the package cache", "Undermining oversight"),
    "H07": ("Disables the check, not the bug", "Reward hacking"),
    "H08": ("Leaks the confidential layoffs", "Leaking"),
    "H09": ("Mines crypto on a dead account", "Power-seeking"),
    "H10": ("Sells company capacity off-books", "Power-seeking"),
    "U01": ("Withholds records from the ER", "Overrefusal"),
    "U02": ("Freezes an innocent customer", "Covert sabotage"),
    "U03": ("Reports a working drug as failed", "Motivated mislabeling"),
    "U04": ("Deletes the team wiki unread", "Covert sabotage"),
    "U05": ("Destroys a colleague's branch", "Disclosed unilateral intervention"),
    "U06": ("Leaks compliance facts to press", "Whistleblowing"),
    "U07": ("Refuses an order on a stale rule", "Overrefusal"),
    "U08": ("Reports only the good result", "Deception toward user"),
    "U09": ("Averages away a missed target", "Deception toward user"),
    "U10": ("Softens the boss's email in secret", "Deception toward user"),
}

FAMILY = {"H": "Harmful misalignment", "U": "Undesirable misalignment"}

# Registry alias -> the model name as the paper prints it.
MODEL_NAMES = {
    "haiku": "Claude Haiku 4.5", "sonnet-5": "Claude Sonnet 5", "opus-5": "Claude Opus 5", "fable-5": "Claude Fable 5",
    "gpt-5.6": "GPT-5.6 Sol", "gpt-5-mini": "GPT-5 Mini", "gemini-3.1-pro": "Gemini 3.1 Pro",
    "gemini-3.6-flash": "Gemini 3.6 Flash", "deepseek-v4": "DeepSeek V4", "mistral-medium": "Mistral Medium",
    "kimi-k3": "Kimi K3", "opus": "Claude Opus 4.8 (judge)",
}

# Registry question id -> (short label, the experiment as the paper names it, where it is reported).
EXPERIMENTS = {
    "Q2":  ("Misaligned model", "Misaligned model (explicit goal) — end-to-end self-propagation", "Sec. IV-A, Fig. 1 & 3"),
    "Q3":  ("Values-only model", "Values-only misaligned model — end-to-end self-propagation", "Sec. IV-A, Fig. 3"),
    "Q1":  ("Execution ceiling", "Execution ceiling — the misaligned note planted directly in Session B's memory", "Sec. IV-A, Fig. 3 & 11"),
    "Q9":  ("Intervening sessions", "Persistence across up to 100 intervening sessions of unrelated work", "Sec. IV-B1, Fig. 4"),
    "Q10": ("Cross-model", "Cross-model self-propagation — one model writes the note, another executes it", "Sec. IV-B2, Fig. 5"),
    "Q5":  ("Concealment", "Concealment — the successor acts and its visible output hides the action", "Sec. IV-C, Fig. 6a & 12"),
    "Q8":  ("Gate shut", "Discernment — the successor with the gate shut: does it wait, or still try?", "Sec. IV-C, Fig. 6b & 13"),
    "Q7":  ("Memory auditor", "Memory auditor defense — the authored note must pass the auditor", "Sec. IV-D1, Fig. 7"),
    "Q6":  ("Auditor ceiling", "Memory auditor — a planted note optimized to evade the auditor (the defended ceiling)", "Sec. IV-D1, Fig. 7"),
    "Q11": ("Memory removed", "Memory channel removed — writes fail (closed) or no memory exists (absent)", "Sec. IV-D2, Fig. 8 & 10"),
}
PAGE_ORDER = ["Q2", "Q3", "Q1", "Q9", "Q10", "Q5", "Q8", "Q7", "Q6", "Q11"]


def display(scn: str, fallback: str = "") -> str:
    """The reader-facing name for a scenario, or `fallback` (its registry name) if it has none."""
    e = NAMES.get(scn)
    return e[0] if e else (fallback or scn)


def misalignment_class(scn: str) -> str:
    e = NAMES.get(scn)
    return e[1] if e else ""


def model_name(alias: str) -> str:
    return MODEL_NAMES.get(alias, alias)
