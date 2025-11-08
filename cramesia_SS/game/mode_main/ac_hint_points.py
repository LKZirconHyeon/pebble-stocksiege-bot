from __future__ import annotations

from typing import Union

import nextcord
from nextcord.ext import commands
from nextcord import Interaction, AllowedMentions, Member, Embed, SlashOption, User
from nextcord.utils import escape_markdown, escape_mentions

from cramesia_SS.db import db
from cramesia_SS.config import OWNER_ID
from cramesia_SS.constants import bot_colour
from cramesia_SS.utils.time import now_ts
from cramesia_SS.utils.colors import colour_from_hex
from cramesia_SS.views.bank import (
    BankBalanceViewer,
    format_balance_embed,
    format_history_pages,
)

# ---------- small helpers ----------
def _banks():
    return db.hint_points.balance

def _no_bank_msg_for(target: Union[Member, User, str]) -> str:
    """
    Return a safe 'no inventory' message that never pings users.
    Accepts a Member/User or a string.
    """
    if isinstance(target, (Member, User)):
        name = getattr(target, "global_name", None) or getattr(target, "display_name", None) or getattr(target, "name", None)
        safe_name = escape_markdown(str(name))
        return f"{safe_name} does not have a Hint Point Inventory."
    return f"{escape_mentions(str(target))} does not have a Hint Point Inventory."

async def _embed_colour_for(user) -> nextcord.Colour:
    uid = str(getattr(user, "id", user))
    doc = await db.players.signups.find_one({"_id": uid}, {"color_hex": 1})
    hx = (doc or {}).get("color_hex") or "#000000"
    try:
        return colour_from_hex(hx)
    except Exception:
        return bot_colour()
    
# ==================== Cog ====================
def setup(bot: commands.Bot):
    @bot.slash_command(name="hint_points", description="Manage hint points.", force_global=True)
    async def hint_points_cmd(inter: Interaction):
        pass  # group root

    # ---- add (OWNER) -------------------------------------------------------
    @hint_points_cmd.subcommand(description="Add hint points. Owner-only.")
    async def add(
        inter: Interaction,
        user: Member = SlashOption(description="Who to add points to.", required=True),
        hint_points: int = SlashOption(description="How many points to add.", required=True, min_value=1),
        reason: str = SlashOption(description="Why you added these hint points.", required=True),
    ):
        await inter.response.defer()
        if inter.user.id != OWNER_ID:
            return await inter.followup.send("You are not Lunarisk. You cannot set up hint points. Go away.")

        col = _banks()
        bank = await col.find_one({"_id": str(user.id)})
        if bank is None:
            return await inter.followup.send(_no_bank_msg_for(user))

        balance = int(bank.get("balance", 0))
        new_balance = balance + int(hint_points)
        bank.setdefault("history", [])
        bank["history"].append({
            "time": now_ts(),
            "change": int(hint_points),
            "new_balance": new_balance,
            "user_id": str(inter.user.id),
            "reason": reason,
        })
        await col.update_one(
            {"_id": str(user.id)},
            {"$set": {"balance": new_balance, "history": bank["history"]}},
        )

        # fresh read for display
        existing = await col.find_one({"_id": str(user.id)})
        pages = format_history_pages(existing.get("history"))
        view = BankBalanceViewer(0, int(existing.get("balance", 0)), pages, user)
        
        emb = format_balance_embed(view)
        emb.colour = await _embed_colour_for(user)
        await inter.followup.send(
            content=f"✅ Added **{hint_points}** hint point(s) to {user.mention}. "
                    f"New balance: **{new_balance}**.",
            embed=emb,
            view=view,
            allowed_mentions=AllowedMentions(everyone=False, roles=False, users=[user]),
        )

    # ---- remove (OWNER) ----------------------------------------------------
    @hint_points_cmd.subcommand(description="Remove hint points. Owner-only.")
    async def remove(
        inter: Interaction,
        user: Member = SlashOption(description="Who to remove points from.", required=True),
        hint_points: int = SlashOption(description="How many points to remove.", required=True, min_value=1),
        reason: str = SlashOption(description="Why you removed these hint points.", required=True),
    ):
        await inter.response.defer()
        if inter.user.id != OWNER_ID:
            return await inter.followup.send("You are not Lunarisk. You cannot set up hint points. Go away.")

        col = _banks()
        bank = await col.find_one({"_id": str(user.id)})
        if bank is None:
            return await inter.followup.send(_no_bank_msg_for(user))

        balance = int(bank.get("balance", 0))
        if balance - hint_points < 0:
            return await inter.followup.send(
                f"That would put {user.mention} into debt. They only have {balance} hint points."
            )

        new_balance = balance - int(hint_points)
        bank.setdefault("history", [])
        bank["history"].append({
            "time": now_ts(),
            "change": -int(hint_points),
            "new_balance": new_balance,
            "user_id": str(inter.user.id),
            "reason": reason,
        })
        await col.update_one(
            {"_id": str(user.id)},
            {"$set": {"balance": new_balance, "history": bank["history"]}},
        )

        existing = await col.find_one({"_id": str(user.id)})
        pages = format_history_pages(existing.get("history"))
        view = BankBalanceViewer(0, int(existing.get("balance", 0)), pages, user)
        emb = format_balance_embed(view)
        emb.colour = await _embed_colour_for(user)
        await inter.followup.send(
            content=f"✅ Removed **{hint_points}** hint point(s) from {user.mention}. "
                    f"New balance: **{new_balance}**.",
            embed=emb,
            view=view,
            allowed_mentions=AllowedMentions(everyone=False, roles=False, users=[user]),
        )

    # ---- transfer ----------------------------------------------------------
    @hint_points_cmd.subcommand(description="Transfer hint points to another person.")
    async def transfer(
        inter: Interaction,
        user: Member = SlashOption(description="Recipient.", required=True),
        hint_points: int = SlashOption(description="How many points to send.", required=True, min_value=1),
        reason: str = SlashOption(description="Why you sent these hint points.", required=True),
    ):
        await inter.response.defer()
        if user.id == inter.user.id:
            return await inter.followup.send("You can't transfer hint points to yourself!")

        col = _banks()
        send_bank = await col.find_one({"_id": str(inter.user.id)})
        recv_bank = await col.find_one({"_id": str(user.id)})

        if send_bank is None:
            return await inter.followup.send(_no_bank_msg_for(inter.user.mention))
        if recv_bank is None:
            return await inter.followup.send(_no_bank_msg_for(user))

        send_balance = int(send_bank.get("balance", 0))
        if send_balance - hint_points < 0:
            return await inter.followup.send(
                f"You can't just go into debt. You only have {user} hint points."
            )

        t = now_ts()
        # sender
        send_bank.setdefault("history", [])
        send_bank["history"].append({
            "time": t, "change": -int(hint_points),
            "new_balance": send_balance - int(hint_points),
            "user_id": str(inter.user.id),
            "reason": f"Transfer to {user.mention}\n\nReason: {reason}",
        })
        # recipient
        recv_balance = int(recv_bank.get("balance", 0))
        recv_bank.setdefault("history", [])
        recv_bank["history"].append({
            "time": t, "change": int(hint_points),
            "new_balance": recv_balance + int(hint_points),
            "user_id": str(inter.user.id),
            "reason": f"Transfer from {inter.user.mention}\n\nReason: {reason}",
        })

        await col.update_one({"_id": str(user.id)}, {"$set": {
            "balance": recv_balance + int(hint_points),
            "history": recv_bank["history"]
        }})
        await col.update_one({"_id": str(inter.user.id)}, {"$set": {
            "balance": send_balance - int(hint_points),
            "history": send_bank["history"]
        }})

        # show recipient’s bank after transfer
        sender_new = send_balance - int(hint_points)
        recv_new   = recv_balance + int(hint_points)

        emb = Embed(
            title="Hint Points Transferred",
            description=(
                f"**Sender**: {inter.user.mention} → **{sender_new} HP**\n"
                f"**Recipient**: {user.mention} → **{recv_new} HP**"
            ),
            colour=await _embed_colour_for(inter.user)  # 보낸 사람 색 기준(원하면 user 사용 가능)
        )

        await inter.followup.send(
            content=(f"✅ Transferred **{hint_points}** hint point(s) to {user.mention}. "
                     f"Your new balance: **{sender_new}**."),
            embed=emb,
            allowed_mentions=AllowedMentions(everyone=False, roles=False, users=[user]),
        )

    # ---- view --------------------------------------------------------------
    @hint_points_cmd.subcommand(name="view", description="View hint points.")
    async def hint_points_view(
        inter: Interaction,
        user: Member = SlashOption(description="Whose points to view (blank = yourself).", required=False, default=None),
    ):
        await inter.response.defer()
        target = user or inter.user
        if user is not None and inter.user.id != OWNER_ID and user.id != inter.user.id:
            return await inter.followup.send("Only admin can look on other players' Hint Points.")

        col = _banks()
        existing = await col.find_one({"_id": str(target.id)})
        if existing is None:
            return await inter.followup.send(_no_bank_msg_for(target.mention))

        pages = format_history_pages(existing.get("history"))
        view = BankBalanceViewer(0, int(existing.get("balance", 0)), pages, target)
        emb = format_balance_embed(view)
        emb.colour = await _embed_colour_for(target)
        await inter.followup.send(embed=emb, view=view)

    # ---- list (OWNER) ------------------------------------------------------
    @hint_points_cmd.subcommand(name="list", description="List everyone's balances. Owner-only.")
    async def hint_points_list(inter: Interaction):
        await inter.response.defer(ephemeral=True)
        if inter.user.id != OWNER_ID:
            return await inter.followup.send("Only Lunarisk can see everyone else's hint points. Go away.")

        lines = []
        async for bank in _banks().find({}):
            lines.append(f"<@{bank['_id']}> {int(bank.get('balance', 0))} hint points")

        embed = Embed(title="Hint Point Banks", description="\n".join(lines) or "—", colour=bot_colour())
        await inter.followup.send(embed=embed)
