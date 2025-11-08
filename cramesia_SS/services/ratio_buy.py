# cramesia_SS/services/ratio_buy.py
from __future__ import annotations
import re
from typing import List, Tuple, Dict
from cramesia_SS.constants import MAX_ITEM_UNITS

# --- local-safe helpers (no circular import) ---------------------------------
def _resolve_item_code(items_cfg: dict, ident: str) -> str | None:
    """Accepts code ('A'..'H') or item name; returns canonical code or None."""
    s = str(ident).strip()
    if s in items_cfg:
        return s
    # try match by name (case-insensitive)
    s_low = s.lower()
    for code, info in items_cfg.items():
        if str(info.get("name", "")).lower() == s_low:
            return code
    return None

def _shown_price(item: dict, use_next: bool) -> int:
    """Return the price currently *shown* to players (NEXT if enabled)."""
    if use_next and ("next_price" in item) and item["next_price"] is not None:
        return int(item["next_price"])
    return int(item.get("price", 0))

# --- public API ---------------------------------------------------------------
def detect_ratio_mode(raw: str) -> bool:
    """True if input uses only ':' or ';' separators (ratio mode)."""
    s = (raw or "").strip()
    has_ratio = bool(re.search(r"[:;]", s))
    has_pair  = bool(re.search(r"[,|]", s))
    if has_ratio and has_pair:
        raise ValueError("You cannot mix ':' or ';' with ',' or '|' in the same order.")
    return has_ratio

def parse_ratio_orders(raw: str) -> List[Tuple[str, int]]:
    """
    Segments split by ':' or ';'
      each segment: '<ident> <weight>'  or  '<weight> <ident>'
    Examples: 'A 1:B 2:C 1'   /   'A 1;B 2;C 1'
    Returns: list[(ident, weight>=1)]
    """
    if not raw or not raw.strip():
        raise ValueError("No ratio orders found.")
    segs = [c.strip() for c in re.split(r"[:;]+", raw.strip()) if c.strip()]
    out: List[Tuple[str, int]] = []
    for seg in segs:
        m = (re.match(r"^(?P<id>.+?)\s+(?P<w>\d+)$", seg)
             or re.match(r"^(?P<w>\d+)\s+(?P<id>.+)$", seg))
        if not m:
            raise ValueError(f"Cannot parse ratio segment: `{seg}` (use 'A 2' or '2 A').")
        ident = re.sub(r"\s+", " ", m.group("id").strip())
        w = int(m.group("w"))
        if w <= 0:
            raise ValueError(f"Weight must be >= 1 in segment `{seg}`.")
        out.append((ident, w))
    if not out:
        raise ValueError("No valid ratio segments.")
    return out

def ratio_buy_plan(
    *,
    items_cfg: dict,
    use_next: bool,
    holdings_now: Dict[str, int],
    cash_now: int,
    pairs: List[Tuple[str, int]],
) -> Tuple[List[str], Dict[str, int], int]:
    """
    Build a ratio-based buy plan.

    Rules:
      - allocate floor(cash * weight / sumW) to each item
      - buy floor(budget / price) (price 0 ⇒ skip)
      - pool leftovers, then buy extra 1개씩 from the cheapest upward while affordable
      - respect MAX_ITEM_UNITS
    Returns: (lines, new_holdings, total_spent)
    """
    # 0) normalize & price map
    plan: List[Tuple[str, int, int]] = []  # (code, weight, price)
    total_w = 0
    for ident, w in pairs:
        code = _resolve_item_code(items_cfg, ident)
        if not code:
            raise ValueError(f"Unknown item: `{ident}`")
        px = _shown_price(items_cfg[code], use_next)
        plan.append((code, w, max(0, int(px))))
        total_w += w
    if total_w <= 0:
        raise ValueError("Weights sum to zero.")

    holdings_now = {str(k): int(v) for k, v in (holdings_now or {}).items()}
    buy_units: Dict[str, int] = {c: 0 for c, _, _ in plan}
    budget: Dict[str, int] = {}
    spent = 0

    # 1) primary allocation
    for code, w, px in plan:
        alloc = (cash_now * w) // total_w
        budget[code] = alloc
        if px <= 0 or alloc < px:
            continue
        max_afford = alloc // px
        room = max(0, MAX_ITEM_UNITS - int(holdings_now.get(code, 0)))
        units = min(max_afford, room)
        if units <= 0:
            continue
        buy_units[code] += units
        cost = units * px
        budget[code] -= cost
        spent += cost

    # 2) leftovers pooled → greedy extra by cheapest first
    pool = sum(budget.values())
    priced = [(c, px) for c, _, px in plan if px > 0]
    priced.sort(key=lambda x: x[1])

    while pool > 0:
        progressed = False
        for code, px in priced:
            if px > pool:
                continue
            have = int(holdings_now.get(code, 0)) + buy_units.get(code, 0)
            if have >= MAX_ITEM_UNITS:
                continue
            buy_units[code] += 1
            pool -= px
            spent += px
            progressed = True
            if pool <= 0:
                break
        if not progressed:
            break

    # 3) build results
    new_holdings = dict(holdings_now)
    lines: List[str] = []
    for code, _, px in plan:
        u = int(buy_units.get(code, 0))
        if u <= 0:
            if px == 0:
                lines.append(f"⚠️ {code}: price is 0 — skipped")
            else:
                lines.append(f"• {code}: 0 (unaffordable or capped)")
            continue
        new_holdings[code] = int(new_holdings.get(code, 0)) + u
        lines.append(f"✅ {code} × {u} @ {px:,} = {(u*px):,}")

    return lines, new_holdings, spent
