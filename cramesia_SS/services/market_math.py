# cramesia_SS/services/market_math.py
from __future__ import annotations
from typing import Dict, Iterable, Mapping

from cramesia_SS.constants import ODDS, ITEM_CODES

def calculate_odds(years: Iterable[dict], odds_table: Mapping[int, int] | None = None) -> Dict[str, int]:
    """
    1:1 with the original bot:
    - Start each stock A..H at 50.
    - Iterate years in ascending `_id`.
    - For each stock in a year, add ODDS[percent] to its score.
    - Clamp to 0..100.
    """
    table = ODDS if odds_table is None else dict(odds_table)
    out: Dict[str, int] = {s: 50 for s in ITEM_CODES}
    years_sorted = sorted(years, key=lambda y: y.get("_id", 0))
    for y in years_sorted:
        for s in ITEM_CODES:
            if s not in y:
                continue
            try:
                change = int(y[s])
            except Exception:
                continue
            out[s] = max(0, min(100, out[s] + int(table.get(change, 0))))
    return out
