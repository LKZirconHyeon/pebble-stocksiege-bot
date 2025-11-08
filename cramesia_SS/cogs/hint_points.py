from typing import Union
import nextcord
from nextcord.ext import commands
from nextcord import Interaction, AllowedMentions, Embed, Member, User
from nextcord.utils import escape_markdown, escape_mentions

from cramesia_SS.db import hint_points
from cramesia_SS.config import OWNER_ID
from cramesia_SS.views.bank import BankBalanceViewer
from cramesia_SS.utils.paginate import paginate_list
from cramesia_SS.utils.time import now_ts

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

class HintPoints(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @nextcord.slash_command(name="hint_points", description="Manage your hint points", force_global=True)
    async def root(self, inter: Interaction): pass

    @root.subcommand(description="Add hint points")
    async def add(self, inter: Interaction, user: Member, hint_points: int, reason: str):
        await inter.response.defer()
        if inter.user.id != OWNER_ID:
            return await inter.followup.send("You are not Lunarisk. Go away.")
        col = hint_points_db = hint_points.balance
        bank = await col.find_one({"_id": str(user.id)})
        if not bank: return await inter.followup.send(_no_bank_msg_for(user))
        history_entry = {"time": now_ts(), "change": hint_points,
                         "new_balance": bank["balance"] + hint_points,
                         "user_id": str(inter.user.id), "reason": reason}
        bank["history"].append(history_entry)
        await col.update_one({"_id": str(user.id)},
                             {"$set": {"balance": bank["balance"] + hint_points, "history": bank["history"]}})
        bank = await col.find_one({"_id": str(user.id)})
        bank["history"].sort(key=lambda x: x["time"], reverse=True)
        pages = paginate_list(bank["history"])
        view = BankBalanceViewer(0, bank["balance"], pages, user)
        await inter.followup.send(f"Added {hint_points} points to {user.mention}", view=view, embed=view.children[0].view._fmt_balance(view))  # type: ignore

def setup(bot: commands.Bot):
    bot.add_cog(HintPoints(bot))
