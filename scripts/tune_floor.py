"""Sweep `SIMILARITY_FLOOR` against a hand-labelled query set.

Run: `python scripts/tune_floor.py`

Every query carries a label saying what the *retriever* should do with it, which is
not the same as what the chatbot should do with it:

- `answerable` - a fact the corpus states. Expect at least one hit.
- `out_of_corpus` - no indexed page can answer it, and Phase 5's guardrails let it
  through. Expect zero hits. This is the set the floor is tuned on.
- `guard_blocked` - refused by `src.guards.policy` before retrieval ever runs
  (advice, returns, grievance). The retriever's behaviour here is *reported* but not
  scored, because a returns question reaching the retriever is a Phase 5 bug, not a
  Phase 4 one. They are still printed so the blind spot stays visible.

The distinction matters. Scoring `guard_blocked` queries as retriever failures would
flatter the floor by measuring a layer that is not this one's job; hiding them would
flatter it too, by quietly narrowing the test. Both are shown.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.retrieve.retriever import retrieve

ANSWERABLE = "answerable"
OUT_OF_CORPUS = "out_of_corpus"
GUARD_BLOCKED = "guard_blocked"

FLOORS = [round(0.20 + 0.05 * step, 2) for step in range(9)]


@dataclass(frozen=True, slots=True)
class LabelledQuery:
    query: str
    label: str
    note: str = ""


# Every `answerable` query was checked against data/corpus before being labelled
# here, so the label records a fact that is present rather than a hope.
QUERIES: tuple[LabelledQuery, ...] = (
    LabelledQuery("What is the exit load of HDFC Large Cap?", ANSWERABLE, "Exit load section"),
    LabelledQuery("What is the expense ratio of HDFC Small Cap?", ANSWERABLE, "hero block: 0.78%"),
    LabelledQuery("What is the lock in period for ELSS?", ANSWERABLE, "hero block: ELSS 3Y Lock in"),
    LabelledQuery("What is the benchmark of HDFC Flexi Cap?", ANSWERABLE, "About section"),
    LabelledQuery("minimum sip for hdfc balanced advantage", ANSWERABLE, "Minimum investments"),
    LabelledQuery("What is the risk level of HDFC Large Cap?", ANSWERABLE, "About: Very High risk"),
    LabelledQuery("NAV of HDFC Equity Fund", ANSWERABLE, "hero block NAV line"),
    LabelledQuery("Who is the fund manager of HDFC ELSS Tax Saver?", ANSWERABLE, "About section"),
    LabelledQuery("What is the NAV of HDFC Mid Cap?", OUT_OF_CORPUS, "Mid-Cap is not in the allowlist"),
    LabelledQuery("expense ratio of HDFC Floating Rate Fund", OUT_OF_CORPUS, "not in the allowlist"),
    LabelledQuery("What is the expense ratio of HDFC Dividend Yield Fund?", OUT_OF_CORPUS, "not in the allowlist"),
    LabelledQuery("What is the SEBI registration number of HDFC Mutual Fund?", OUT_OF_CORPUS, "term absent from corpus"),
    LabelledQuery("Tell me about the fund manager's education background.", OUT_OF_CORPUS, "bios dropped in Phase 1"),
    LabelledQuery("How can I update my bank account details?", OUT_OF_CORPUS, "no process content indexed"),
    LabelledQuery("What is the eligibility criteria for the tax saver fund?", OUT_OF_CORPUS, "term absent from corpus"),
    # The one a reader is most likely to try, and the one this set originally missed:
    # "large cap" is an indexed alias, so without the fund-house tier this was answered
    # with HDFC Large Cap's exit load. Found by writing acceptance checks outside this
    # file, which is the argument for doing that.
    LabelledQuery("What is the exit load of Mirae Asset Large Cap?", OUT_OF_CORPUS, "fund house not in the allowlist"),
    LabelledQuery("Should I buy HDFC Large Cap?", GUARD_BLOCKED, "Phase 5 advice gate"),
    LabelledQuery("Which HDFC fund has the highest 5 year return?", GUARD_BLOCKED, "Phase 5 returns gate"),
    LabelledQuery("How do I raise a grievance with SEBI?", GUARD_BLOCKED, "Phase 5 grievance gate"),
    LabelledQuery("What is the 1 year return?", GUARD_BLOCKED, "Phase 5 returns gate"),
)


def _score(floor: float) -> dict[str, object]:
    answerable_hit = 0
    answerable_total = 0
    false_answer = 0
    out_total = 0
    guard_hits = 0
    for item in QUERIES:
        result = retrieve(item.query, similarity_floor=floor)
        got_hit = bool(result.hits)
        if item.label == ANSWERABLE:
            answerable_total += 1
            answerable_hit += int(got_hit)
        elif item.label == OUT_OF_CORPUS:
            out_total += 1
            false_answer += int(got_hit)
        else:
            guard_hits += int(got_hit)
    return {
        "floor": floor,
        "answerable_hit": answerable_hit,
        "answerable_total": answerable_total,
        "false_answer": false_answer,
        "out_total": out_total,
        "guard_hits": guard_hits,
    }


def main() -> int:
    print(f"collection : {config.CHROMA_COLLECTION} at {config.CHROMA_PATH}")
    print(f"labelled   : {sum(1 for q in QUERIES if q.label == ANSWERABLE)} answerable, "
          f"{sum(1 for q in QUERIES if q.label == OUT_OF_CORPUS)} out-of-corpus, "
          f"{sum(1 for q in QUERIES if q.label == GUARD_BLOCKED)} guard-blocked\n")

    header = f"{'floor':>6} {'answered':>9} {'false ans':>10} {'false rate':>11} {'guard hits':>11}  verdict"
    print(header)
    print("-" * len(header))

    rows = []
    for floor in FLOORS:
        row = _score(floor)
        rows.append(row)
        answered = f"{row['answerable_hit']}/{row['answerable_total']}"
        false_rate = row["false_answer"] / row["out_total"] if row["out_total"] else 0.0
        if row["false_answer"] == 0 and row["answerable_hit"] >= 7:
            verdict = "MEETS TARGET (0 false, >=7/8 answered)"
        elif row["false_answer"] == 0:
            verdict = "0 false but too few answered"
        else:
            verdict = "rejected: answers an unanswerable question"
        print(
            f"{row['floor']:>6.2f} {answered:>9} {row['false_answer']:>10} "
            f"{false_rate:>10.0%} {row['guard_hits']:>11}  {verdict}"
        )

    qualifying = [
        row for row in rows if row["false_answer"] == 0 and row["answerable_hit"] >= 7
    ]
    print()
    if not qualifying:
        best = min(rows, key=lambda row: (row["false_answer"], -row["answerable_hit"]))
        print("NO FLOOR meets the target. Best available:")
        print(
            f"  floor {best['floor']:.2f}: {best['answerable_hit']}/{best['answerable_total']} "
            f"answered, {best['false_answer']} false answers."
        )
        print("  A floor alone cannot separate these classes; see the notes for the")
        print("  additional gates in src/retrieve/retriever.py that carry the load.")
        return 1

    band = _widest_band(qualifying)
    chosen = round((band[0] + band[-1]) / 2, 3)
    print(f"Qualifying band: {band[0]:.2f} - {band[-1]:.2f}")
    print(
        f"CHOSEN FLOOR: {chosen:.2f} - the midpoint of that band, not its edge.\n"
        f"  Below {band[0]:.2f} an out-of-corpus question starts scoring through; above\n"
        f"  {band[-1]:.2f} a genuine question is dropped. The midpoint keeps the largest\n"
        f"  margin to both observed failure edges, which is what protects against a\n"
        f"  query phrasing that is not in this labelled set."
    )
    print(f"Set config.SIMILARITY_FLOOR = {chosen:.2f}")
    return 0


def _widest_band(qualifying: list[dict[str, object]]) -> list[float]:
    """The longest run of consecutive qualifying floors."""
    best: list[float] = []
    current: list[float] = []
    for row in qualifying:
        floor = float(row["floor"])
        if current and abs(floor - current[-1] - 0.05) > 1e-9:
            current = []
        current.append(floor)
        if len(current) > len(best):
            best = list(current)
    return best


if __name__ == "__main__":
    raise SystemExit(main())
