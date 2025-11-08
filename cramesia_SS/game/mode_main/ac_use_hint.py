# cramesia_SS/game/mode_main/ac_use_hint.py
from __future__ import annotations

import nextcord
from nextcord.ext import commands
from nextcord import Interaction, SlashOption

from cramesia_SS.db import db
from cramesia_SS.constants import ITEM_CODES, ODDS, ODDS_APOC
from cramesia_SS.utils.guards import guard, disallow_self_hint_when_eliminated
from cramesia_SS.utils.time import now_ts
from cramesia_SS.constants import bot_colour
from cramesia_SS.utils.colors import colour_from_hex
from cramesia_SS.services.market_math import calculate_odds
from cramesia_SS.views.bank import (
    BankBalanceViewer,
    format_balance_embed,
    format_history_pages,
)

# ---------- collection & config helpers ----------
def _cfg():
    return db.market.config

def _changes():
    return db.stocks.changes

def _banks():
    return db.hint_points.balance

async def _get_market_config() -> dict | None:
    return await _cfg().find_one({"_id": "current"})

async def _mode_is(name: str) -> bool:
    cfg = await _get_market_config() or {}
    return (cfg.get("game_mode") or "classic").lower() == name.lower()

async def _trading_locked() -> bool:
    cfg = await _get_market_config() or {}
    return bool(cfg.get("trading_locked"))

def _item_label(code: str, items_cfg: dict) -> str:
    info = (items_cfg or {}).get(code, {})
    return f"{code} — {info.get('name', code)}"

def _apoc_bucket(pct: int) -> str:
    n = abs(int(pct))
    if n <= 10:
        return "Low"
    if n <= 25:
        return "Medium"
    return "High"

async def _embed_colour_for(user) -> nextcord.Colour:
    uid = str(getattr(user, "id", user))
    doc = await db.players.signups.find_one({"_id": uid}, {"color_hex": 1})
    hx = (doc or {}).get("color_hex") or "#000000"
    try:
        return colour_from_hex(hx)
    except Exception:
        return bot_colour()
    
# ============================= Cog ===========================================
def setup(bot: commands.Bot):

    @bot.slash_command(
        name="use_hint",
        description="Use your hint points",
        force_global=True,
    )
    async def use_hint(inter: Interaction):
        pass  # group root

    # --------------------- R-hint -------------------------------------------
    @use_hint.subcommand(name="r", description="Reveal odds of all stocks. Costs 1 HP.")
    @guard(require_private=True, public=True, require_unlocked=True)
    @disallow_self_hint_when_eliminated(public=True)
    async def r_hint(
        inter: Interaction,
        confirm: str = SlashOption(description="put R HINT in this to proceed.", required=True),
    ):
        if not inter.response.is_done():
            await inter.response.defer()
        send = inter.followup.send

        # gates
        if await _mode_is("apocalypse"):
            await send("⛔ R-hint is **disabled** in Apocalypse mode.")
            return
        if confirm != "R HINT":
            await send("Command rejected. Put ``R HINT`` in the ``confirm`` option to use an r hint.")
            return
        if await _trading_locked():
            await send("❌ Hint usage is temporarily locked by the host.")
            return

        # bank
        col = _banks()
        bank = await col.find_one({"_id": str(inter.user.id)})
        if bank is None:
            await send("You need to sign up first using /signup join.")
            return
        bank.setdefault("history", [])

        # not enough balance
        if int(bank.get("balance", 0)) < 1:
            pages = format_history_pages(bank.get("history"))
            view = BankBalanceViewer(0, int(bank.get("balance", 0)), pages, inter.user)
            emb = format_balance_embed(view)
            emb.colour = await _embed_colour_for(inter.user)
            await send(
                f"You need 1 hint point to use an R hint. You only have {bank.get('balance', 0)} hint points.",
                embed=emb,
                view=view,
            )
            return

        # compute odds (R-hint = history only, exclude the latest year)
        years = [doc async for doc in _changes().find({})]
        years.sort(key=lambda d: d["_id"])

        if len(years) >= 2:
            hist_years = years[:-1]                # drop latest
            odds_map = calculate_odds(hist_years)
        else:
            odds_map = {code: 50 for code in ITEM_CODES}  # no history yet

        # deduct & persist
        bank["balance"] = int(bank.get("balance", 0)) - 1
        bank["history"].append({
            "time": now_ts(), "change": -1, "new_balance": bank["balance"],
            "user_id": str(inter.user.id), "reason": "Used R-hint."
        })
        await col.update_one({"_id": str(inter.user.id)},
                             {"$set": {"balance": bank["balance"], "history": bank["history"]}})

        # pretty output
        items_cfg = (await _get_market_config() or {}).get("items", {})
        lines = [f"{_item_label(code, items_cfg)}: {odds_map.get(code, 50)}%" for code in ITEM_CODES]


        bank["history"].sort(key=lambda x: x["time"], reverse=True)
        pages = format_history_pages(bank.get("history"))
        view = BankBalanceViewer(0, int(bank.get("balance", 0)), pages, inter.user)
        emb = format_balance_embed(view)
        emb.colour = await _embed_colour_for(inter.user)
        await send("Used R-hint!\n\n" + "\n".join(lines), embed=emb, view=view)

    # --------------------- LVL1 ---------------------------------------------
    @use_hint.subcommand(name="lvl1", description="Reveals the strength of change for a single stock. Costs 1 HP.")
    @guard(require_private=True, public=True, require_unlocked=True)
    @disallow_self_hint_when_eliminated(public=True)
    async def lvl1_hint(
        inter: Interaction,
        stock: str = SlashOption(description="Stock", required=True, choices=[s for s in "ABCDEFGH"]),
        confirm: str = SlashOption(description="Type LVL1 to proceed.", required=True),
    ):
        if not inter.response.is_done():
            await inter.response.defer()
        send = inter.followup.send

        if confirm != "LVL1":
            await send("Command rejected. Put ``LVL1`` in the ``confirm`` option to use a hint.")
            return
        if await _trading_locked():
            await send("❌ Hint usage is temporarily locked by the host.")
            return

        col = _banks()
        bank = await col.find_one({"_id": str(inter.user.id)})
        if bank is None:
            await send("You need to sign up first using /signup join.")
            return

        items_cfg = (await _get_market_config() or {}).get("items", {})
        label = _item_label(stock, items_cfg)

        if await _mode_is("apocalypse"):
            docs = [d async for d in _changes().find({}, {"_id": 1, stock: 1})]
            docs.sort(key=lambda d: d["_id"])
            hist = docs[:-1] if docs else []
            prob = 50
            for y in hist:
                try:
                    prob += int(ODDS_APOC.get(int(y.get(stock, 0)), 0))
                except Exception:
                    pass
            prob = max(0, min(100, prob))
            msg = f"Used level 1 hint!\n\n**Chance of LOW fall** for {label}: **{prob}%**"
            cost = 1
        else:
            years = [doc async for doc in _changes().find({})]
            years.sort(key=lambda y: y["_id"])
            if not years:
                await send("There is no stock info in this bot's database.")
                return
            latest = years[-1]
            odds_change = int(ODDS[int(latest[stock])])
            strength = "**Low**" if abs(odds_change) <= 3 else ("**Medium**" if abs(odds_change) <= 9 else "**High**")
            msg = f"Used level 1 hint!\n\nChange of {label}: {strength}"
            cost = 1

        bal = int(bank.get("balance", 0))
        if bal < cost:
            bank["history"].sort(key=lambda x: x["time"], reverse=True)
            pages = format_history_pages(bank.get("history"))
            view = BankBalanceViewer(0, bal, pages, inter.user)
            emb = format_balance_embed(view)
            emb.colour = await _embed_colour_for(inter.user)
            await send(f"You need {cost} hint point(s). You only have {bal}.", embed=emb, view=view)
            return

        bank["balance"] = bal - cost
        bank["history"].append({"time": now_ts(), "change": -cost, "new_balance": bank["balance"],
                                "user_id": str(inter.user.id), "reason": f"Used level 1 hint on {stock}."})
        await col.update_one({"_id": str(inter.user.id)},
                             {"$set": {"balance": bank["balance"], "history": bank["history"]}})
        bank["history"].sort(key=lambda x: x["time"], reverse=True)
        pages = format_history_pages(bank.get("history"))
        view = BankBalanceViewer(0, int(bank.get("balance", 0)), pages, inter.user)
        emb = format_balance_embed(view)
        emb.colour = await _embed_colour_for(inter.user)
        await send(msg, embed=emb, view=view)

    # --------------------- LVL2 ---------------------------------------------
    @use_hint.subcommand(name="lvl2", description="Gives 2 possible changes for a stock. Costs 2 HP")
    @guard(require_private=True, public=True, require_unlocked=True)
    @disallow_self_hint_when_eliminated(public=True)
    async def lvl2_hint(
        inter: Interaction,
        stock: str = SlashOption(description="Stock", required=True, choices=[s for s in "ABCDEFGH"]),
        confirm: str = SlashOption(description="Type LVL2 to proceed.", required=True),
    ):
        if not inter.response.is_done():
            await inter.response.defer()
        send = inter.followup.send

        if confirm != "LVL2":
            await send("Command rejected. Put ``LVL2`` in the ``confirm`` option to use a hint.")
            return
        if await _trading_locked():
            await send("❌ Hint usage is temporarily locked by the host.")
            return

        col = _banks()
        bank = await col.find_one({"_id": str(inter.user.id)})
        if bank is None:
            await send("You need to sign up first using /signup join.")
            return

        items_cfg = (await _get_market_config() or {}).get("items", {})
        label = _item_label(stock, items_cfg)

        if await _mode_is("apocalypse"):
            docs = [d async for d in _changes().find({}, {"_id": 1, stock: 1})]
            docs.sort(key=lambda d: d["_id"])
            if not docs or stock not in docs[-1]:
                await send("No stock info in this bot's database.")
                return
            change = int(docs[-1][stock])
            bucket = _apoc_bucket(change).upper()
            msg = f"Used level 2 hint!\n\n**Fall strength for {label}: {bucket}**"
            cost = 2
        else:
            years = [doc async for doc in _changes().find({})]
            years.sort(key=lambda y: y["_id"])
            latest = years[-1]
            stock_change = int(latest[stock])
            odds_change = int(ODDS[stock_change])
            opposite = next((int(c) for c, oc in ODDS.items() if int(oc) == -odds_change), stock_change)
            a, b = sorted([stock_change, opposite])
            msg = f"Used level 2 hint!\n\nPossible changes for {label}: **{a}%, {b}%**"
            cost = 2

        bal = int(bank.get("balance", 0))
        if bal < cost:
            bank["history"].sort(key=lambda x: x["time"], reverse=True)
            pages = format_history_pages(bank.get("history"))
            view = BankBalanceViewer(0, bal, pages, inter.user)
            emb = format_balance_embed(view)
            emb.colour = await _embed_colour_for(inter.user)
            await send(f"You need {cost} hint point(s). You only have {bal}.", embed=emb, view=view)
            return

        bank["balance"] = bal - cost
        bank["history"].append({"time": now_ts(), "change": -cost, "new_balance": bank["balance"],
                                "user_id": str(inter.user.id), "reason": f"Used level 2 hint on {stock}."})
        await col.update_one({"_id": str(inter.user.id)},
                             {"$set": {"balance": bank["balance"], "history": bank["history"]}})
        bank["history"].sort(key=lambda x: x["time"], reverse=True)
        pages = format_history_pages(bank.get("history"))
        view = BankBalanceViewer(0, int(bank.get("balance", 0)), pages, inter.user)
        emb = format_balance_embed(view)
        emb.colour = await _embed_colour_for(inter.user)
        await send(msg, embed=emb, view=view)

    # --------------------- LVL3 ---------------------------------------------
    @use_hint.subcommand(name="lvl3", description="Shows whether a stock will increase or decrease. Costs 3 HP")
    @guard(require_private=True, public=True, require_unlocked=True)
    @disallow_self_hint_when_eliminated(public=True)
    async def lvl3_hint(
        inter: Interaction,
        stock: str = SlashOption(description="Stock", required=True, choices=[s for s in "ABCDEFGH"]),
        confirm: str = SlashOption(description="Type LVL3 to proceed.", required=True),
    ):
        if not inter.response.is_done():
            await inter.response.defer()
        send = inter.followup.send

        if confirm != "LVL3":
            await send("Command rejected. Put ``LVL3`` in the ``confirm`` option to use a hint.")
            return
        if await _trading_locked():
            await send("❌ Hint usage is temporarily locked by the host.")
            return

        col = _banks()
        bank = await col.find_one({"_id": str(inter.user.id)})
        if bank is None:
            await send("You need to sign up first using /signup join.")
            return

        items_cfg = (await _get_market_config() or {}).get("items", {})
        label = _item_label(stock, items_cfg)

        if await _mode_is("apocalypse"):
            docs = [d async for d in _changes().find({}, {"_id": 1, stock: 1})]
            docs.sort(key=lambda d: d["_id"])
            if not docs or stock not in docs[-1]:
                await send("There is no stock info in this bot's database.")
                return
            change = int(docs[-1][stock])
            msg = f"Used level 3 hint!\n\n**Exact fall for {label}: {change}%**"
            cost = 3
        else:
            years = [doc async for doc in _changes().find({})]
            years.sort(key=lambda y: y["_id"])
            latest = years[-1]
            v = int(latest[stock])
            info = f"{label} will **increase**" if v > 0 else (f"{label} will **decrease**" if v < 0 else f"{label} will **not change in price**")
            msg = "Used level 3 hint!\n\n" + info
            cost = 3

        bal = int(bank.get("balance", 0))
        if bal < cost:
            bank["history"].sort(key=lambda x: x["time"], reverse=True)
            pages = format_history_pages(bank.get("history"))
            view = BankBalanceViewer(0, bal, pages, inter.user)
            emb = format_balance_embed(view)
            emb.colour = await _embed_colour_for(inter.user)
            await send(f"You need {cost} hint point(s). You only have {bal}.", embed=emb, view=view)
            return

        bank["balance"] = bal - cost
        bank["history"].append({"time": now_ts(), "change": -cost, "new_balance": bank["balance"],
                                "user_id": str(inter.user.id), "reason": f"Used level 3 hint on {stock}."})
        await col.update_one({"_id": str(inter.user.id)},
                             {"$set": {"balance": bank["balance"], "history": bank["history"]}})
        bank["history"].sort(key=lambda x: x["time"], reverse=True)
        pages = format_history_pages(bank.get("history"))
        view = BankBalanceViewer(0, int(bank.get("balance", 0)), pages, inter.user)
        emb = format_balance_embed(view)
        emb.colour = await _embed_colour_for(inter.user)
        await send(msg, embed=emb, view=view)
