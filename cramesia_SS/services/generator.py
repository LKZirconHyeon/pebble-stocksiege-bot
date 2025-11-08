# cramesia_SS/services/generator.py
from __future__ import annotations
from typing import Dict, List, Tuple, Optional, Iterable
import random, time, json, hashlib
from nextcord import Embed
from cramesia_SS.services.market_math import calculate_odds


from cramesia_SS.db import db
from cramesia_SS.constants import (
    ITEM_CODES, bot_colour,
    UP_TABLE, DOWN_TABLE, ZERO_VALUES,        # ZERO_VALUES currently unused but kept for clarity
    ETU_RATIOS, ETU_ODDS_NEUTRAL_MIN, ETU_ODDS_NEUTRAL_MAX,
    ODDS,                                      # owner-odds adjustment by latest year
)

# ---------------- DB helpers ----------------
def _cfg():
    return db.market.config

def _changes():
    return db.stocks.changes

# ---------------- odds (Owner/R-hint) ----------------
async def _years_sorted() -> List[dict]:
    """Return all year docs sorted by _id (int)."""
    years = [doc async for doc in _changes().find({}, {})]
    years.sort(key=lambda d: int(d["_id"]))
    return years

async def compute_rhint_odds() -> dict[str, int]:
    years, ldb = await _timeline()
    if len(years) < 2:                      # 최신 locked 직전까지가 최소 1년 있어야 함
        return {c: 50 for c in ITEM_CODES}
    return calculate_odds(years[:-1])       # 최신 locked(DB==n) 제외 → n-1까지

async def compute_owner_odds() -> dict[str, int]:
    years, ldb = await _timeline()
    if not years:
        return {c: 50 for c in ITEM_CODES}
    base_map = await compute_rhint_odds() if len(years) >= 2 else {c: 50 for c in ITEM_CODES}
    latest = years[-1]                      # 최신 locked(DB==n)
    out: dict[str, int] = {}
    for c in ITEM_CODES:
        base = int(base_map.get(c, 50))
        adj = int(ODDS.get(int(latest.get(c, 0)), 0))   # Year n 변동으로 보정
        out[c] = max(0, min(100, base + adj))
    return out

# ---------------- signed-diff rule ----------------
Group = str  # 'UP_LOW'|'UP_MED'|'UP_HIGH'|'DOWN_LOW'|'DOWN_MED'|'DOWN_HIGH'|'ZERO'

def classify_signed_diff(p: int, rng: random.Random) -> tuple[Group, int, int, Optional[int]]:
    """
    Signed-diff classification.
    p: up-odds (1..100)
    return: (group, d, r, forced_delta or None)

    Finalized rules:
      d = r - p
      d == 0 or d == +1  → ZERO = 0%
      -14..-1   → UP_LOW
      +2..+15   → DOWN_LOW
      -29..-15  → UP_MED
      +16..+30  → DOWN_MED
      -44..-30  → UP_HIGH
      +31..+45  → DOWN_HIGH
      d <= -45  → FORCED +400%
      d >= +46  → FORCED -80%
    """
    r = rng.randint(1, 100)
    d = r - p

    if d == 0 or d == 1:
        return "ZERO", d, r, 0

    if d <= -45:
        return "UP_HIGH", d, r, 400
    if d >= +46:
        return "DOWN_HIGH", d, r, -80

    if -14 <= d <= -1:
        return "UP_LOW", d, r, None
    if +2 <= d <= +15:
        return "DOWN_LOW", d, r, None

    if -29 <= d <= -15:
        return "UP_MED", d, r, None
    if +16 <= d <= +30:
        return "DOWN_MED", d, r, None

    if -44 <= d <= -30:
        return "UP_HIGH", d, r, None
    if +31 <= d <= +45:
        return "DOWN_HIGH", d, r, None

    return "ZERO", d, r, 0

def _weighted_choice(candidates: Dict[int, int], rng: random.Random) -> int:
    ks, ws = list(candidates.keys()), list(candidates.values())
    return rng.choices(ks, weights=ws, k=1)[0]

def choose_delta(group: Group, rng: random.Random, forced: Optional[int]) -> int:
    """Return final delta % (forced has the highest priority)."""
    if forced is not None:
        return forced
    if group == "ZERO":
        return 0
    side = "UP" if group.startswith("UP") else "DOWN"
    band = "LOW" if group.endswith("LOW") else ("MED" if group.endswith("MED") else "HIGH")
    table = UP_TABLE if side == "UP" else DOWN_TABLE
    cand = table.get(band) or {}
    if not cand:
        # graceful fallback if a table is empty
        for alt in ("MED", "LOW", "HIGH"):
            cand = table.get(alt) or {}
            if cand:
                break
    return _weighted_choice(cand, rng) if cand else 0

# --- NEW: simple global ETU (mismatch vs match) -----------------------------
def compute_etu_simple(rows: List[dict], odds_map: Dict[str, int]) -> dict:
    """
    Global ETU index based on expected vs actual sides.
    - Eligible set: p <= 40 or p >= 60 (exclude 41..59)
    - expected_side: 'UP' if p >= 60 else 'DOWN'
    - actual_side  : 'UP' if delta>0, 'DOWN' if delta<0, 'ZERO' if delta==0
    - mismatch if actual_side != expected_side (ZERO counts as mismatch)
    - warn if mismatches >= matches
    Returns:
      {
        'eligible': int,
        'match': int,
        'mismatch': int,
        'warn': bool
      }
    """
    match = mismatch = eligible = 0
    # rows are in A..H order; odds_map has codes -> p
    for r in rows:
        code = r["code"]
        p = int(odds_map.get(code, 50))
        if 41 <= p <= 59:
            continue  # not eligible
        eligible += 1
        expected = "UP" if p >= 60 else "DOWN"
        actual = "UP" if r["delta"] > 0 else ("DOWN" if r["delta"] < 0 else "ZERO")
        if actual == expected:
            match += 1
        else:
            mismatch += 1
    return {
        "eligible": eligible,
        "match": match,
        "mismatch": mismatch,
        "warn": mismatch >= match
    }

# ---------------- common ----------------
def _checksum(payload: dict) -> str:
    b = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(b).hexdigest()

async def _next_year_auto() -> int:
    cfg = await _cfg().find_one(
        {"_id": "current"},
        {"last_result_year": 1, "next_year": 1, "use_next_for_total": 1},
    ) or {}

    nx = int(cfg.get("next_year") or 0)
    use_next = bool(cfg.get("use_next_for_total"))
    locked = await _latest_locked_year()

    base = nx if (use_next and nx > 0) else (locked if locked >= 1 else 1)
    return min(11, base + 1)

# ---------------- main entry ----------------
async def generate_preview_or_commit(*, year: Optional[int], dry_run: bool) -> Dict:

    # ---- season-end guard (mainstream only) --------------------------------
    cfg_now = await _cfg().find_one(
        {"_id": "current"},
        {"game_mode": 1, "next_year": 1, "use_next_for_total": 1}
    ) or {}
    is_battle = str(cfg_now.get("game_mode", "classic")).lower() == "battle"

    if not is_battle:
        locked_latest = await _latest_locked_year()
        if locked_latest >= 11:
            raise RuntimeError(
                "Season complete (DB 11 = Year 10 results). "
                "Use /stock_change finalize or /signup reset."
            )
        if bool(cfg_now.get("use_next_for_total")) and int(cfg_now.get("next_year") or 0) >= 11:
            raise RuntimeError(
                "Season has been completed (DB 11). "
                "Finalize and reset — further generation is not allowed."
            )

    required = await _next_year_auto()
    if (not is_battle) and required > 11:
        raise RuntimeError("Reached final year (11). Season complete — use /signup reset to start a new season.")

    if year is None:
        year = required
    if int(year) != int(required):
        raise RuntimeError(f"Year must be {required} (sequential only).")

    await _guard_no_unrevealed_pending(int(year))

    existing = await _changes().find_one({"_id": int(year)})
    if existing and existing.get("locked") and not dry_run:
        raise RuntimeError("This season is locked. Use /signup reset.")

    rng = random.Random(time.time_ns())
    cfg = await _cfg().find_one({"_id": "current"}) or {}
    items_cfg: dict = cfg.get("items") or {}
    name_by_code = {c: (items_cfg.get(c) or {}).get("name", c) for c in ITEM_CODES}

    owner_map = await compute_owner_odds()

    rows: List[Dict] = []
    for code in ITEM_CODES:
        p = int(owner_map.get(code, 50))
        group, d, r, forced = classify_signed_diff(p, rng)
        delta = choose_delta(group, rng, forced)
        rows.append({
            "code": code, "name": name_by_code[code],
            "up_prob": p, "rand": r, "diff": d,
            "group": group, "delta": int(delta), "forced": bool(forced is not None),
        })

    p_map_preview = {r["code"]: int(r["up_prob"]) for r in rows}
    etu_simple = compute_etu_simple(rows, p_map_preview)

    payload = {
        "year": int(year),
        "source": "auto",
        "generated_at": int(time.time()),
        "stocks": rows,
        "etu_simple": etu_simple,
    }
    payload["checksum"] = _checksum(payload)

    if dry_run:
        return {"preview": True, **payload}

    doc_changes = {c: int(next((r["delta"] for r in rows if r["code"] == c), 0)) for c in ITEM_CODES}
    await _changes().update_one(
        {"_id": int(year)},
        {"$set": {**doc_changes, "meta": {k: v for k, v in payload.items() if k not in ("stocks",)}, "locked": True}},
        upsert=True
    )
    return {"preview": False, **payload}

# ---------------- preview embed ----------------
def build_preview_embed(doc: Dict) -> Embed:
    """
    Final version:
    - Show only A→H full list (no Top↑/Top↓/0%)
    - ETU warning only if eligible >= 4
    - Styled bilingual warning message
    """
    e = Embed(
        title=f"Preview — Year {doc['year']} changes",
        colour=bot_colour(),
    )

    # Sort by A→H
    row_by_code = {s["code"]: s for s in doc["stocks"]}
    ordered_rows = [row_by_code[c] for c in ITEM_CODES if c in row_by_code]

    # Full list only (A→H) — show percent + [r|p|d]
    all_lines = [
        f"`{r['code']}` {r['name']}: **{r['delta']}%**"
        + (" (FORCED)" if r["forced"] else "")
        + f"  _[r={r['rand']}, p={r['up_prob']}, d={r['diff']:+}]_"
        for r in ordered_rows
    ]

    e.add_field(
        name="All items (A→H)",
        value="\n".join(all_lines) if all_lines else "No data.",
        inline=False
    )

    # ---- ETU (threshold = eligible >= 4) ----
    etu = doc.get("etu_simple", {"eligible": 0, "match": 0, "mismatch": 0, "warn": False})
    eligible = etu.get("eligible", 0)
    match = etu.get("match", 0)
    mismatch = etu.get("mismatch", 0)

    # New warning condition: only trigger if eligible >= 4
    warn = eligible >= 4 and mismatch >= match

    etu_line = f"eligible={eligible} • match={match} • mismatch={mismatch}"
    if warn:
        e.add_field(
            name="ETU",
            value=(
                f"{etu_line}\n"
                "⚠ **EXPECT THE UNEXPECTED!**\n"
                "Review twice before confirming — "
                "otherwise Re-roll or Cancel!"
            ),
            inline=False,
        )
    else:
        e.add_field(name="ETU", value=etu_line, inline=False)

    return e

# Snapshot Commit
async def commit_preview(preview_doc: Dict) -> Dict:
    """
    Commit exactly what was shown in the preview:
    - No RNG rerun.
    - Writes A..H deltas from preview_doc['stocks'].
    - Validates year drift & lock.
    """
    # 1) Basic validation
    if not isinstance(preview_doc, dict) or "year" not in preview_doc or "stocks" not in preview_doc:
        raise RuntimeError("Invalid preview document.")

    year = int(preview_doc["year"])

    # 2) Year drift guard (must still be the next auto year)
    auto_year = await _next_year_auto()
    if year != auto_year:
        raise RuntimeError(f"Year drift detected (now {auto_year}). Run generate again.")

    # 3) Lock guard
    existing = await _changes().find_one({"_id": year})
    if existing and existing.get("locked"):
        raise RuntimeError("This season is already locked.")

    # 4) Build A..H fields from preview rows
    rows: List[Dict] = list(preview_doc["stocks"])
    doc_changes = {c: int(next((r["delta"] for r in rows if r["code"] == c), 0)) for c in ITEM_CODES}

    # 5) Persist (keep metadata from preview; remove 'preview' flag)
    meta = {k: v for k, v in preview_doc.items() if k not in ("stocks", "preview")}
    await _changes().update_one(
        {"_id": year},
        {"$set": {
            **doc_changes,
            "meta": meta,
            "locked": True,
        }},
        upsert=True
    )

    # 6) Return committed document-ish payload
    return {"preview": False, **preview_doc, "locked": True}

async def _guard_no_unrevealed_pending(required_year: int) -> None:
    """
    Block generation if there is a locked doc for the same `required_year`
    that has NOT been revealed into totals yet.

    Allowed to proceed only if:
      use_next_for_total is True AND next_year == required_year
    """
    cfg = await _cfg().find_one(
        {"_id": "current"},
        {"next_year": 1, "use_next_for_total": 1},
    ) or {}
    nx = int(cfg.get("next_year") or 0)
    use_next = bool(cfg.get("use_next_for_total"))

    pending = await _changes().find_one({"_id": int(required_year)})
    if pending:
        if not (use_next and nx == int(required_year)):
            raise RuntimeError(
                f"Year {required_year} is already generated and locked. "
                "Run /stock_change reveal_next (and settle) before generating the following year."
            )
        
async def _timeline() -> tuple[list[dict], int]:
    """
    Return (years_locked, l_db) where:
      - l_db = max _id of changes with locked==True
      - years_locked = all change docs with _id <= l_db, sorted asc
    """
    docs = [d async for d in _changes().find({}, {})]
    locked_docs = [d for d in docs if bool(d.get("locked"))]
    if not locked_docs:
        return [], 0
    locked_docs.sort(key=lambda d: int(d["_id"]))
    l_db = int(locked_docs[-1]["_id"])
    years = [d for d in docs if int(d.get("_id", 0)) <= l_db]
    years.sort(key=lambda d: int(d["_id"]))
    return years, l_db

async def _latest_locked_year() -> int:
    doc = await _changes().find_one({"locked": True}, sort=[("_id", -1)])
    return int(doc["_id"]) if doc else 0