# cramesia_SS/utils/guards.py
from __future__ import annotations
import functools
from typing import Callable, Awaitable

import nextcord
from nextcord import Interaction

from cramesia_SS.db import db
from cramesia_SS.config import OWNER_ID  # use config constant, not db.config


# ---------------------- generic guard ----------------------
def guard(
    require_private: bool = True,
    public: bool = False,
    require_unlocked: bool = False,
    owner_only: bool = False,
):
    def decorator(func: Callable[..., Awaitable]):
        @functools.wraps(func)
        async def wrapper(inter: Interaction, *args, **kwargs):
            # ---- Cheap checks that don't need awaits (respond immediately) ----
            if require_private and inter.guild is not None and not public:
                if inter.response.is_done():
                    await inter.followup.send("❌ Use this command in DMs.", ephemeral=True)
                else:
                    await inter.response.send_message("❌ Use this command in DMs.", ephemeral=True)
                return

            if owner_only and inter.user.id != int(OWNER_ID):
                if inter.response.is_done():
                    await inter.followup.send("❌ Owner only.", ephemeral=True)
                else:
                    await inter.response.send_message("❌ Owner only.", ephemeral=True)
                return

            # ---- We’re about to do awaits (DB) — keep the token alive ----
            if not inter.response.is_done():
                # if the command is public, do a public defer; otherwise ephemeral
                try:
                    await inter.response.defer(ephemeral=not public)
                except Exception:
                    # if it was already acknowledged by someone else, ignore
                    pass

            # ---- Expensive checks (DB) ----
            if require_unlocked:
                cfg = await db.market.config.find_one({"_id": "current"}) or {}
                if cfg.get("trading_locked"):
                    await inter.followup.send("❌ Trading is currently locked.", ephemeral=not public)
                    return

            return await func(inter, *args, **kwargs)
        return wrapper
    return decorator


async def _mode_is(mode: str) -> bool:
    cfg = await db.market.config.find_one({"_id": "current"}, {"game_mode": 1}) or {}
    return str(cfg.get("game_mode", "")).lower() == mode.lower()


def requires_mode(mode: str, *, public: bool = False):
    def decorator(func: Callable[..., Awaitable]):
        @functools.wraps(func)
        async def wrapper(inter: Interaction, *args, **kwargs):
            # pre-defer before the DB read
            if not inter.response.is_done():
                try:
                    await inter.response.defer(ephemeral=not public)
                except Exception:
                    pass

            if not await _mode_is(mode):
                await inter.followup.send(f"❌ This command is only available in **{mode}** mode.", ephemeral=not public)
                return
            return await func(inter, *args, **kwargs)
        return wrapper
    return decorator


def disallow_self_hint_when_eliminated(*, public: bool = False):
    def decorator(func: Callable[..., Awaitable]):
        @functools.wraps(func)
        async def wrapper(inter: Interaction, *args, **kwargs):
            # pre-defer before reading the portfolio
            if not inter.response.is_done():
                try:
                    await inter.response.defer(ephemeral=not public)
                except Exception:
                    pass

            uid = str(inter.user.id)
            pf = await db.market.portfolios.find_one({"_id": uid}, {"eliminated": 1})
            if pf and bool(pf.get("eliminated")):
                await inter.followup.send("⛔ You are **eliminated** and cannot use hints.", ephemeral=not public)
                return
            return await func(inter, *args, **kwargs)
        return wrapper
    return decorator


__all__ = ["guard", "requires_mode", "disallow_self_hint_when_eliminated", "_mode_is"]