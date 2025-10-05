# cramesia_SS/game/mode_main/ac_market.py
from __future__ import annotations
from typing import Dict, List, Tuple

from nextcord.ext import commands
from nextcord import Interaction, SlashOption, Embed, Member

from cramesia_SS.db import db
from cramesia_SS.config import OWNER_ID
from cramesia_SS.constants import (
    bot_colour, ITEM_CODES, ODDS, MAX_ITEM_UNITS
)
from cramesia_SS.utils.guards import guard, requires_mode
from cramesia_SS.utils.time import now_ts
from cramesia_SS.utils.text import fmt_price
from cramesia_SS.utils.colors import colour_from_hex

# ---------- collections ----------
def _cfg():
    return db.market.config
def _ports():
    return db.market.portfolios
def _changes():
    return db.stocks.changes
def _snaps():
    return db.market.snapshots

# ---------- helpers ----------
def _resolve_item_code(items: dict, ident: str) -> str | None:
    if not ident:
        return None
    s = ident.strip()
    up = s.upper()
    if up in ITEM_CODES:
        return up
    import re
    norm = re.sub(r"\s+", " ", s).strip().casefold()
    for code in ITEM_CODES:
        info = (items.get(code) or {})
        nm_norm = re.sub(r"\s+", " ", str(info.get("name", ""))).strip().casefold()
        if nm_norm == norm:
            return code
        for alias in (info.get("aliases") or []):
            if re.sub(r"\s+", " ", str(alias)).strip().casefold() == norm:
                return code
    return None

def _parse_orders(raw: str) -> list[tuple[str, int]]:
    """
    Accept pairs separated by comma or pipe only.
    Each pair is either:
      - "<ident> <qty>"  (e.g., "A 5", "Zinc 10")
      - "<qty> <ident>"  (e.g., "5 A", "10 Zinc")
    """
    import re
    if not raw or not raw.strip():
        raise ValueError("No orders found.")

    pairs: list[tuple[str, int]] = []
    # Only comma or pipe as separators
    chunks = [c.strip() for c in re.split(r"[,\|]+", raw.strip()) if c.strip()]

    for s in chunks:
        m = (
            re.match(r"^(?P<ident>.+?)\s+(?P<qty>\d+)$", s) or
            re.match(r"^(?P<qty>\d+)\s+(?P<ident>.+)$", s)
        )
        if not m:
            raise ValueError(
                f"Cannot parse pair: `{s}` (use 'A 10' or '10 A'; pairs separated by comma or pipe)."
            )
        ident = re.sub(r"\s+", " ", m.group("ident").strip())
        qty = int(m.group("qty"))
        if qty < 1 or qty > MAX_ITEM_UNITS:
            raise ValueError(f"Quantity out of range for `{s}` (1–{MAX_ITEM_UNITS}).")
        pairs.append((ident, qty))

    if not pairs:
        raise ValueError("No valid (item, quantity) pairs found.")
    return pairs

async def _get_config() -> dict:
    doc = await _cfg().find_one({"_id": "current"}) or {}
    doc.setdefault("items", {c: {"name": c, "price": 0} for c in ITEM_CODES})
    doc.setdefault("trading_locked", False)
    doc.setdefault("last_result_year", 0)
    return doc

async def _set_trading_locked(flag: bool) -> None:
    await _cfg().update_one(
        {"_id": "current"},
        {"$set": {"trading_locked": bool(flag), "updated_at": now_ts()}},
        upsert=True
    )

def _shown_price(item: dict, use_next: bool) -> int:
    return int(item.get("next_price" if use_next else "price", 0))

def _portfolio_totals(pf: dict, items: dict, use_next: bool) -> tuple[int, int, int]:
    """
    Returns (cash, holdings_value, total_cash) using shown price (next/current).
    """
    cash = int(pf.get("cash", 0))
    holdings = pf.get("holdings") or {}
    hv = 0
    for code, qty in holdings.items():
        q = int(qty or 0)
        if q <= 0:
            continue
        it = items.get(code) or {}
        hv += q * _shown_price(it, use_next)
    return cash, hv, cash + hv

async def _latest_pre_for_next(next_year: int | None) -> dict | None:
    """
    Newest snapshot for this 'next_year' taken before liquidation.
    Prefers the single 'revert' copy, else 'pre_reveal'. Falls back on taken_at/created_at.
    """
    q = {"type": {"$in": ["revert", "pre_reveal"]}}
    if next_year:
        q["result_year"] = int(next_year)
    return await _snaps().find_one(q, sort=[("taken_at", -1), ("created_at", -1)])

def _snap_price_for(code: str, snap: dict) -> int:
    it = (snap.get("items") or {}).get(code, {}) if snap else {}
    # pre_reveal snapshots store the “current shown” price in 'price';
    # revert may carry use_next_for_total if needed; be defensive:
    use_next_in_snap = bool(snap.get("use_next_for_total"))
    return int(it.get("next_price" if use_next_in_snap else "price", it.get("price", 0)))

def _fmt_change_line(old: int, new: int) -> str:
    delta = new - old
    if old > 0:
        pct = (delta / old) * 100.0
        return f"{fmt_price(old)} → {fmt_price(new)}  =  {('+' if delta>=0 else '')}{fmt_price(delta)} ({pct:+.2f}%)"
    return f"{fmt_price(old)} → {fmt_price(new)}  =  {('+' if delta>=0 else '')}{fmt_price(delta)}"

from cramesia_SS.config import ALLOWED_GAME_CATEGORY_ID  # add this

def _category_ok(inter: Interaction) -> bool:
    """True if command is used in the allowed category (or restriction is disabled)."""
    try:
        cat_id = int(ALLOWED_GAME_CATEGORY_ID or 0)
    except Exception:
        cat_id = 0
    if cat_id <= 0:
        return True  # restriction disabled
    # in guild channels, check category id
    return getattr(inter.channel, "category_id", None) == cat_id

async def _enforce_market_channel(inter: Interaction) -> bool:
    """Send a polite error if channel is not allowed. Return True if OK to proceed."""
    if _category_ok(inter):
        return True
    try:
        msg = "❌ This command can only be used in the designated Stock Siege category."
        # if you want to show the category mention, uncomment (Discord doesn’t support category mentions):
        # msg += f" (<#{ALLOWED_GAME_CATEGORY_ID}>)"
        if inter.response.is_done():
            await inter.followup.send(msg, ephemeral=True)
        else:
            await inter.response.send_message(msg, ephemeral=True)
    except Exception:
        pass
    return False

# ===================== Cog =====================
def setup(bot: commands.Bot):

    @bot.slash_command(name="market", description="Market tools", force_global=True)
    async def market_root(inter: Interaction):
        pass

    # ---- view --------------------------------------------------------------
    @market_root.subcommand(name="view", description="View current items and prices.")
    @guard(require_private=False, public=True)
    async def market_view(inter: Interaction):
        if not inter.response.is_done():
            await inter.response.defer()
    
        cfg = await _get_config()
        items: Dict[str, dict] = cfg["items"]
        use_next = bool(cfg.get("use_next_for_total"))
    
        lines = [
            f"{c}: **{info.get('name','?')}** — {fmt_price(_shown_price(info, use_next))}"
            for c, info in items.items()
        ]
    
        title = "Market — Items" + (" (Next-Year Prices)" if use_next else "")
    
        emb = Embed(
            title=title,
            description="\n".join(lines) if lines else "— No items —",
            colour=bot_colour()
        )
        await inter.followup.send(embed=Embed(title=title, description="\n".join(lines), colour=bot_colour()))

    # ---- portfolio ---------------------------------------------------------
    @market_root.subcommand(name="inv", description="View your portfolio with total value.")
    @guard(require_private=False, public=True)
    async def market_portfolio(inter: Interaction):

        if not await _enforce_market_channel(inter):
            return
        
        if not inter.response.is_done():
            await inter.response.defer()

        uid = str(inter.user.id)
        pf = await _ports().find_one({"_id": uid})
        if not pf:
            return await inter.followup.send("❌ You don't have an Inventory yet. Use `/signup join` first.")

        cfg = await _get_config()
        items = cfg["items"]
        use_next = bool(cfg.get("use_next_for_total"))

        cash, hv, total = _portfolio_totals(pf, items, use_next)

        # Show old→new only while NEXT is active (after reveal_next, before liquidate)
        change_block = ""
        next_year = int(cfg.get("next_year") or 0)
        if use_next and next_year:
            snap = await _latest_pre_for_next(next_year)
            if snap:
                # find this user in the snapshot
                old_pf = next((p for p in snap.get("portfolios", []) if str(p.get("_id")) == uid), None)
                if old_pf:
                    old_cash = int(old_pf.get("cash", 0))
                    old_hv = 0
                    for code in ITEM_CODES:
                        q = int((old_pf.get("holdings", {}) or {}).get(code, 0))
                        if q > 0:
                            old_hv += q * _snap_price_for(code, snap)
                    old_total = old_cash + old_hv
                    change_block = (
                        "\n**Since last snapshot**\n"
                        f"Total: {_fmt_change_line(old_total, total)}"
                    )

        lines = [
            f"**Unspent Cash**: {fmt_price(cash)}",
            f"**Holdings Value**: {fmt_price(hv)}",
            f"**Total Cash**: {fmt_price(total)}",
        ]
        if change_block:
            lines.append(change_block)
        lines.append("")  # spacer

        # item breakdown
        for c in ITEM_CODES:
            q = int((pf.get("holdings", {}) or {}).get(c, 0))
            if q > 0:
                px = _shown_price(items.get(c, {}), use_next)
                lines.append(f"{c} — {q} × {fmt_price(px)} = {fmt_price(q*px)}")

        # ---- colorized title from signup
        signup = await db.players.signups.find_one({"_id": uid}, {"color_name": 1, "color_hex": 1})
        color_name = (signup or {}).get("color_name") or inter.user.display_name
        color_hex  = (signup or {}).get("color_hex") or "#000000"
        emb_colour = colour_from_hex(color_hex)

        # optional note of who this color belongs to
        owner_line = f"_Signed by:_ {inter.user.mention}\n\n"

        emb = Embed(
            title=f"Portfolio — {color_name}",
            description=owner_line + "\n".join(lines),
            colour=emb_colour,
        )
        await inter.followup.send(embed=emb)


    # ---- buy ---------------------------------------------------------------
    @market_root.subcommand(name="buy", description="Buy items.")
    @guard(require_private=False, public=True, require_unlocked=True)
    async def market_buy(
        inter: Interaction,
        orders: str = SlashOption(description="Buy items using this command! Use comma (,) or pipe (|) to separate items.", required=True),
    ):
        if not await _enforce_market_channel(inter):
            return
        
        if not inter.response.is_done():
            await inter.response.defer()
        
        uid = str(inter.user.id)
        pf = await _ports().find_one({"_id": uid})
        if not pf:
            return await inter.followup.send("❌ You don't have an Inventory yet. Use `/signup join` first.")
    
        cfg = await _get_config()
        items = cfg["items"]
        use_next = bool((await _get_config()).get("use_next_for_total"))
        try:
            pairs = _parse_orders(orders)
        except ValueError as e:
            return await inter.followup.send(f"❌ {e}")
    
        cash = int(pf.get("cash", 0))
        holdings = dict(pf.get("holdings", {}) or {})
        total_cost = 0
        applied: list[str] = []
    
        for ident, qty in pairs:
            code = _resolve_item_code(items, ident)
            if not code:
                return await inter.followup.send(f"❌ Unknown item: `{ident}`")
            px = _shown_price(items[code], use_next)
            cost = px * qty
            total_cost += cost
            new_qty = int(holdings.get(code, 0)) + qty
            if new_qty > MAX_ITEM_UNITS:
                return await inter.followup.send(f"❌ Max units per item is {MAX_ITEM_UNITS} (violated by {code}).")
            holdings[code] = new_qty
            applied.append(f"{code} × {qty} @ {fmt_price(px)} = {fmt_price(cost)}")
    
        if total_cost > cash:
            return await inter.followup.send(
                f"❌ Not enough cash. Need {fmt_price(total_cost)}, you have {fmt_price(cash)}."
            )
    
        new_cash = cash - total_cost
        await _ports().update_one(
            {"_id": uid},
            {"$set": {"cash": new_cash, "holdings": holdings, "updated_at": now_ts()},
             "$push": {"history": {"t": now_ts(), "type": "buy", "orders": pairs}}}
        )
        await inter.followup.send(
            "✅ Bought:\n" + "\n".join(applied) +
            f"\n**Total**: {fmt_price(total_cost)}\n**Unspent Cash**: {fmt_price(new_cash)}"
        )

    # ---- sell --------------------------------------------------------------
    @market_root.subcommand(name="sell", description="Sell items.")
    @guard(require_private=False, public=True, require_unlocked=True)
    async def market_sell(
        inter: Interaction,
        orders: str = SlashOption(description="Sell items using this command! Use comma (,) or pipe (|) to separate items.", required=True),
    ):
        
        if not await _enforce_market_channel(inter):
            return
        
        if not inter.response.is_done():
            await inter.response.defer()

        uid = str(inter.user.id)
        pf = await _ports().find_one({"_id": uid})
        if not pf:
            return await inter.followup.send("❌ You don't have an Inventory yet. Use `/signup join` first.")
    
        cfg = await _get_config()
        items = cfg["items"]
        use_next = bool((await _get_config()).get("use_next_for_total"))
        try:
            pairs = _parse_orders(orders)
        except ValueError as e:
            return await inter.followup.send(f"❌ {e}")
    
        cash = int(pf.get("cash", 0))
        holdings = dict(pf.get("holdings", {}) or {})
        total_income = 0
        applied: list[str] = []
    
        for ident, qty in pairs:
            code = _resolve_item_code(items, ident)
            if not code:
                return await inter.followup.send(f"❌ Unknown item: `{ident}`")
            cur = int(holdings.get(code, 0))
            if qty > cur:
                return await inter.followup.send(f"❌ You only have {cur} units of {code}.")
            px = _shown_price(items[code], use_next)
            income = px * qty
            holdings[code] = cur - qty
            total_income += income
            applied.append(f"{code} × {qty} @ {fmt_price(px)} = {fmt_price(income)}")
    
        new_cash = cash + total_income
        await _ports().update_one(
            {"_id": uid},
            {"$set": {"cash": new_cash, "holdings": holdings, "updated_at": now_ts()},
             "$push": {"history": {"t": now_ts(), "type": "sell", "orders": pairs}}}
        )
        await inter.followup.send(
            "✅ Sold:\n" + "\n".join(applied) +
            f"\n**Total**: {fmt_price(total_income)}\n**Unspent Cash**: {fmt_price(new_cash)}"
        )

    # ---- public cash_rank -----------------------------------
    @market_root.subcommand(name="cash_rank", description="Show ranking by Total Cash.")
    @guard(require_private=False, public=True)
    async def market_cash_rank(inter: Interaction):
        
        if not inter.response.is_done():
            await inter.response.defer()

        cfg = await _get_config()
        items = cfg["items"]
        use_next = bool(cfg.get("use_next_for_total"))
        mode = (cfg.get("game_mode") or "classic").lower()

        portfolios = [pf async for pf in _ports().find({})]

        def total_of(pf: dict) -> int:
            return _portfolio_totals(pf, items, use_next)[2]

        portfolios.sort(key=total_of, reverse=True)

        lines = []
        for i, pf in enumerate(portfolios, 1):
            uid = pf["_id"]
            total = total_of(pf)
            tag = " ⛔ ELIM" if (mode == "elimination" and pf.get("eliminated")) else ""
            lines.append(f"{i}. <@{uid}> — Total Cash: {fmt_price(total)}{tag}")

        title = "Cash Ranking" + (" (Elimination Mode)" if mode == "elimination" else "")
        emb = Embed(title=title, description="\n".join(lines) or "—", colour=bot_colour())
        await inter.followup.send(embed=emb)


    # ---- admin buy ----------------------------------------------------
    @market_root.subcommand(name="admin_buy", description="OWNER: Buy items for a player.")
    @guard(require_private=True, public=True, require_unlocked=True, owner_only=True)
    async def market_admin_buy(inter: Interaction, user: Member, orders: str):
        
        if not await _enforce_market_channel(inter):
            return
        
        if not inter.response.is_done():
            await inter.response.defer()
        
        cfg = await _get_config(); items = cfg["items"]
        uid = str(user.id)
        use_next = bool((await _get_config()).get("use_next_for_total"))
        pf = await _ports().find_one({"_id": uid})
        if not pf:
            return await inter.followup.send(f"❌ {user.mention} has no Inventory.")
        try:
            pairs = _parse_orders(orders)
        except ValueError as e:
            return await inter.followup.send(f"❌ {e}")
    
        holdings = dict(pf.get("holdings", {}) or {})
        cash = int(pf.get("cash", 0))
        total_cost = 0
        lines: List[str] = []
    
        for ident, qty in pairs:
            code = _resolve_item_code(items, ident)
            if not code:
                lines.append(f"❌ Unknown item: {ident}")
                continue
            px = _shown_price(items[code], use_next)
            cost = px * qty
            holdings[code] = int(holdings.get(code, 0)) + qty
            cash -= cost
            total_cost += cost
            lines.append(f"✅ {code}: +{qty} @ {fmt_price(px)}")
    
        await _ports().update_one({"_id": uid},
            {"$set": {"holdings": holdings, "cash": cash, "updated_at": now_ts()}}
        )
        await inter.followup.send(
            "\n".join(lines) + f"\n**Total**: -{fmt_price(total_cost)}\n**Unspent Cash**: {fmt_price(cash)}"
        )
    # ---- admin sell ----------------------------------------------------
    @market_root.subcommand(name="admin_sell", description="OWNER: Sell items for a player.")
    @guard(require_private=True, public=True, require_unlocked=True, owner_only=True)
    async def market_admin_sell(inter: Interaction, user: Member, orders: str):
        
        if not await _enforce_market_channel(inter):
            return
        
        if not inter.response.is_done():
            await inter.response.defer()
        
        cfg = await _get_config(); items = cfg["items"]
        uid = str(user.id)
        use_next = bool((await _get_config()).get("use_next_for_total"))
        pf = await _ports().find_one({"_id": uid})
        if not pf:
            return await inter.followup.send(f"❌ {user.mention} has no Inventory.")
        try:
            pairs = _parse_orders(orders)
        except ValueError as e:
            return await inter.followup.send(f"❌ {e}")
    
        holdings = dict(pf.get("holdings", {}) or {})
        cash = int(pf.get("cash", 0))
        total_income = 0
        lines: List[str] = []
    
        for ident, qty in pairs:
            code = _resolve_item_code(items, ident)
            if not code:
                lines.append(f"❌ Unknown item: {ident}")
                continue
            have = int(holdings.get(code, 0))
            if have <= 0:
                lines.append(f"❌ {code}: player has 0")
                continue
            sell_qty = min(have, qty)
            px = _shown_price(items[code], use_next)
            income = px * sell_qty
            holdings[code] = have - sell_qty
            cash += income
            total_income += income
            lines.append(f"✅ {code}: -{sell_qty} @ {fmt_price(px)}")
    
        await _ports().update_one({"_id": uid},
            {"$set": {"holdings": holdings, "cash": cash, "updated_at": now_ts()}}
        )
        await inter.followup.send(
            "\n".join(lines) + f"\n**Total**: +{fmt_price(total_income)}\n**Unspent Cash**: {fmt_price(cash)}"
        )
    # ---- admin inventory ----------------------------------------------------
    @market_root.subcommand(name="admin_inv", description="OWNER: View someone else's portfolio with totals.")
    @guard(require_private=False, public=True, owner_only=True)
    async def market_admin_inv(inter: Interaction, user: Member = SlashOption(description="Player", required=True)):
        
        if not await _enforce_market_channel(inter):
            return
        
        if not inter.response.is_done():
            await inter.response.defer()

        uid = str(user.id)
        pf = await _ports().find_one({"_id": uid})
        if not pf:
            return await inter.followup.send(f"❌ {user.mention} doesn't have an Inventory.")

        cfg = await _get_config()
        items = cfg["items"]
        use_next = bool(cfg.get("use_next_for_total"))

        cash, hv, total = _portfolio_totals(pf, items, use_next)

        change_block = ""
        next_year = int(cfg.get("next_year") or 0)
        if use_next and next_year:
            snap = await _latest_pre_for_next(next_year)
            if snap:
                old_pf = next((p for p in snap.get("portfolios", []) if str(p.get("_id")) == uid), None)
                if old_pf:
                    old_cash = int(old_pf.get("cash", 0))
                    old_hv = 0
                    for code in ITEM_CODES:
                        q = int((old_pf.get("holdings", {}) or {}).get(code, 0))
                        if q > 0:
                            old_hv += q * _snap_price_for(code, snap)
                    old_total = old_cash + old_hv
                    change_block = (
                        "\n**Since last snapshot**\n"
                        f"Total: {_fmt_change_line(old_total, total)}"
                    )

        lines = [
            f"**Unspent Cash**: {fmt_price(cash)}",
            f"**Holdings Value**: {fmt_price(hv)}",
            f"**Total Cash**: {fmt_price(total)}",
        ]
        if change_block:
            lines.append(change_block)
        lines.append("")

        for c in ITEM_CODES:
            q = int((pf.get("holdings", {}) or {}).get(c, 0))
            if q > 0:
                px = _shown_price(items.get(c, {}), use_next)
                lines.append(f"{c} — {q} × {fmt_price(px)} = {fmt_price(q*px)}")

        # ---- colorized title from signup
        signup = await db.players.signups.find_one({"_id": uid}, {"color_name": 1, "color_hex": 1})
        color_name = (signup or {}).get("color_name") or user.display_name
        color_hex  = (signup or {}).get("color_hex") or "#000000"
        emb_colour = colour_from_hex(color_hex)

        owner_line = f"_Signed by:_ {user.mention}\n\n"

        emb = Embed(
            title=f"Portfolio — {color_name}",
            description=owner_line + "\n".join(lines),
            colour=emb_colour,
        )
        await inter.followup.send(embed=emb)


    # ---- admin: lock/unlock trading ---------------------------------------
    @market_root.subcommand(name="lock_trading", description="OWNER: Lock trading for everyone.")
    @guard(require_private=False, public=True, owner_only=True)
    async def market_lock_trading(inter: Interaction):
        if not inter.response.is_done():
            await inter.response.defer()
        await _set_trading_locked(True)
        await inter.followup.send("🔒 Trading has been **locked**.")

    @market_root.subcommand(name="unlock_trading", description="OWNER: Unlock trading.")
    @guard(require_private=False, public=True, owner_only=True)
    async def market_unlock_trading(inter: Interaction):
        if not inter.response.is_done():
            await inter.response.defer()
        await _set_trading_locked(False)
        await inter.followup.send("🔓 Trading has been **unlocked**.")
    
        # ---- clear (self or owner-target) --------------------------------------
    @market_root.subcommand(
        name="clear",
        description="Clear inventories and refund at shown prices (self; owner can target others).",
    )
    @guard(require_private=False, public=True)  # allow in server; no trading-lock check (siege tool)
    async def market_clear(
        inter: Interaction,
        user: Member = SlashOption(description="(Owner) Clear this player's inventory", required=False, default=None),
    ):
        
        if not await _enforce_market_channel(inter):
            return
        
        if not inter.response.is_done():
            await inter.response.defer()

        # who are we clearing?
        target = user or inter.user
        if user is not None and inter.user.id != OWNER_ID:
            return await inter.followup.send("❌ Only the owner can clear another player's inventory.")

        # fetch portfolio
        uid = str(target.id)
        pf = await _ports().find_one({"_id": uid})
        if not pf:
            return await inter.followup.send(f"❌ {target.mention} has no Inventory. Use `/signup join` first.")

        # read current config and which price is 'shown'
        cfg = await _get_config()
        items = cfg["items"]
        use_next = bool(cfg.get("use_next_for_total"))

        # compute refund at SHOWN price and zero out holdings
        holdings = dict(pf.get("holdings") or {})
        refund = 0
        breakdown_lines = []
        for code, qty in holdings.items():
            q = int(qty or 0)
            if q <= 0:
                continue
            px = _shown_price(items.get(code, {}), use_next)
            refund += q * px
            if q > 0:
                breakdown_lines.append(f"{code} — {q} × {fmt_price(px)} = {fmt_price(q*px)}")
            holdings[code] = 0

        new_cash = int(pf.get("cash", 0)) + refund
        await _ports().update_one(
            {"_id": uid},
            {"$set": {"cash": new_cash, "holdings": holdings, "updated_at": now_ts()},
             "$push": {"history": {"t": now_ts(), "type": "clear", "amount": refund, "by": str(inter.user.id)}}}
        )

        title = f"Inventory Cleared — {target.display_name}"
        desc = [
            f"**Refunded (at shown prices):** {fmt_price(refund)}",
            f"**Unspent Cash (new):** {fmt_price(new_cash)}",
        ]
        if breakdown_lines:
            desc += ["", "*Items refunded:*", *breakdown_lines]

        await inter.followup.send(
            embed=Embed(title=title, description="\n".join(desc), colour=bot_colour())
        )

    # ---- force_cash (owner only) -------------------------------------------
    @market_root.subcommand(
        name="force_cash",
        description="OWNER: Empty holdings and force Unspent Cash to a value. USE THIS ONLY ON LAST RESORT.",
    )
    @guard(require_private=False, public=True, owner_only=True)
    async def market_force_cash(
        inter: Interaction,
        user: Member = SlashOption(description="Player to modify", required=True),
        amount: int = SlashOption(description="New Unspent Cash amount (integer)", required=True, min_value=0),
        note: str = SlashOption(description="Reason / context (shown in history)", required=False, default=""),
    ):
        if not inter.response.is_done():
            await inter.response.defer()

        uid = str(user.id)
        pf = await _ports().find_one({"_id": uid})
        if not pf:
            return await inter.followup.send(f"❌ {user.mention} has no Inventory.")

        # zero holdings; force cash
        zero_holdings = {c: 0 for c in (pf.get("holdings") or {}).keys()}
        await _ports().update_one(
            {"_id": uid},
            {"$set": {"cash": int(amount), "holdings": zero_holdings, "updated_at": now_ts()},
             "$push": {"history": {
                 "t": now_ts(), "type": "force_cash",
                 "amount": int(amount), "by": str(inter.user.id),
                 "note": note or ""
             }}}
        )

        lines = [
            f"**Target:** {user.mention}",
            f"**Unspent Cash (forced):** {fmt_price(int(amount))}",
            f"**Holdings:** cleared to 0 for all items.",
        ]
        if note:
            lines.append(f"**Note:** {note}")

        await inter.followup.send(
            embed=Embed(title="Force Cash Applied", description="\n".join(lines), colour=bot_colour())
        )

