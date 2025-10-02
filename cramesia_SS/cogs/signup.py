import nextcord
from nextcord.ext import commands
from nextcord import Interaction, SlashOption, Embed, Member
from cramesia_SS.db import players, hint_points, market
from cramesia_SS.config import OWNER_ID, ALLOWED_SIGNUP_CHANNEL_ID
from cramesia_SS.constants import (COLOR_NAME_RE, ITEM_CODES, MAX_PLAYERS,
                                   STARTING_CASH, APOC_START_CASH)
from cramesia_SS.utils.colors import normalize_hex, colour_from_hex
from cramesia_SS.utils.time import now_ts

class Signup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @nextcord.slash_command(name="signup", description="Player signup & roster tools", force_global=True)
    async def root(self, inter: Interaction): pass

    @root.subcommand(name="join", description="Sign up and create your hint point inventory (0 pt).")
    async def join(self, inter: Interaction,
                   color_name: str = SlashOption(required=True, max_length=20),
                   color_hex:  str = SlashOption(required=True)):
        ch = inter.channel
        if ALLOWED_SIGNUP_CHANNEL_ID and not (inter.channel_id == ALLOWED_SIGNUP_CHANNEL_ID
                                              or getattr(ch, "parent_id", None) == ALLOWED_SIGNUP_CHANNEL_ID):
            return await inter.response.send_message(
                f"❌ Use this in <#{ALLOWED_SIGNUP_CHANNEL_ID}>.", ephemeral=True)

        if not inter.response.is_done(): await inter.response.defer()

        signups = players.signups; balances = hint_points.balance; ports = market.portfolios
        if await signups.count_documents({}) >= MAX_PLAYERS:
            return await inter.followup.send(f"❌ Capacity full: {MAX_PLAYERS}/{MAX_PLAYERS}.")

        uid = str(inter.user.id)
        if await signups.find_one({"_id": uid}):
            return await inter.followup.send("❌ You have already signed up.")

        # cleanup orphans
        await balances.delete_one({"_id": uid}); await ports.delete_one({"_id": uid})

        if not COLOR_NAME_RE.fullmatch(color_name.strip()):
            return await inter.followup.send("❌ Invalid color name (letters/spaces, ≤20).")
        hex_norm = normalize_hex(color_hex)
        if not hex_norm: return await inter.followup.send("❌ Invalid HEX. Use #RRGGBB.")

        # uniqueness checks
        if await signups.find_one({"color_name": {"$regex": f"^{color_name}$", "$options":"i"}}):
            return await inter.followup.send("❌ Color name already taken.")
        if await signups.find_one({"color_hex": {"$regex": f"^{hex_norm}$", "$options":"i"}}):
            return await inter.followup.send("❌ HEX already used by another player.")

        await signups.insert_one({"_id": uid, "user_id": uid, "user_name": inter.user.name,
                                  "color_name": color_name.strip(), "color_hex": hex_norm, "signup_time": now_ts()})
        await balances.insert_one({"_id": uid, "balance": 0, "history": [{
            "time": now_ts(), "change": 0, "new_balance": 0, "user_id": str(self.bot.user.id),
            "reason": "Signup - inventory opened (0 pt)"}]})

        start_cash = STARTING_CASH  # (read mode in future service if needed)
        await ports.insert_one({"_id": uid, "user_id": uid, "cash": start_cash,
                                "holdings": {c:0 for c in ITEM_CODES}, "updated_at": now_ts(), "history": []})

        remain = MAX_PLAYERS - (await signups.count_documents({}))
        emb = Embed(title="✅ Signup Complete",
                    description=(f"**Player**: {inter.user.mention}\n"
                                 f"**Color**: {color_name} `{hex_norm}`\n"
                                 f"**Starting Cash**: {start_cash}\n"
                                 f"**Slots left**: **{remain}** / {MAX_PLAYERS}"),
                    colour=colour_from_hex(hex_norm))
        await inter.followup.send(embed=emb)

def setup(bot: commands.Bot):
    bot.add_cog(Signup(bot))
