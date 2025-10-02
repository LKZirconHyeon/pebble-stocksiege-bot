# cramesia_SS/services/snapshots.py
from __future__ import annotations

from typing import Dict, Any, List
from cramesia_SS.db import db
from cramesia_SS.constants import ITEM_CODES
from cramesia_SS.utils.time import now_ts

# ----- collections
_snapshots = db.market.snapshots          # ✅ namespaced collection
_cfg       = db.market.config
_ports     = db.market.portfolios

async def _read_items() -> Dict[str, Dict[str, Any]]:
    cfg = await _cfg.find_one({"_id": "current"}) or {}
    return cfg.get("items", {})

async def _read_portfolios() -> List[Dict[str, Any]]:
    return [pf async for pf in _ports.find({})]

async def snapshot_pre_reveal(result_year: int | None) -> str:
    """
    Take a snapshot BEFORE revealing next-year prices.
    Captures: items (current prices), and all portfolios with cash/holdings.
    Returns the inserted snapshot _id as a string.
    """
    items = await _read_items()
    portfolios = await _read_portfolios()

    doc = {
        "type": "pre_reveal",
        "result_year": int(result_year) if result_year is not None else None,
        "taken_at": now_ts(),
        "items": {
            c: {
                "name": items.get(c, {}).get("name"),
                "price": int(items.get(c, {}).get("price", 0)),
            }
            for c in ITEM_CODES
        },
        "portfolios": [
            {
                "_id": str(pf["_id"]),
                "cash": int(pf.get("cash", 0)),
                "holdings": {c: int((pf.get("holdings", {}) or {}).get(c, 0)) for c in ITEM_CODES},
            }
            for pf in portfolios
        ],
    }
    res = await _snapshots.insert_one(doc)   # ✅ write to market.snapshots
    return str(res.inserted_id)

async def snapshot_liquidate(result_year: int | None) -> str:
    """
    Take a snapshot WHEN liquidating (or right before, if you call it first).
    Captures: items (price/next_price), flag use_next_for_total, and portfolios.
    Returns the inserted snapshot _id as a string.
    """
    cfg = await _cfg.find_one({"_id": "current"}) or {}
    items = cfg.get("items", {})
    portfolios = await _read_portfolios()

    doc = {
        "type": "liquidate",
        "result_year": int(result_year) if result_year is not None else None,
        "taken_at": now_ts(),
        "use_next_for_total": bool(cfg.get("use_next_for_total")),
        "items": {
            c: {
                "name": items.get(c, {}).get("name"),
                "price": int(items.get(c, {}).get("price", 0)),
                "next_price": int(items.get(c, {}).get("next_price", items.get(c, {}).get("price", 0))),
            }
            for c in ITEM_CODES
        },
        "portfolios": [
            {
                "_id": str(pf["_id"]),
                "cash": int(pf.get("cash", 0)),
                "holdings": {c: int((pf.get("holdings", {}) or {}).get(c, 0)) for c in ITEM_CODES},
            }
            for pf in portfolios
        ],
    }
    res = await _snapshots.insert_one(doc)   # ✅ write to market.snapshots
    return str(res.inserted_id)

__all__ = ["snapshot_pre_reveal", "snapshot_liquidate"]
