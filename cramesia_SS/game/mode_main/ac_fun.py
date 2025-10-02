# cramesia_SS/game/mode_main/ac_fun.py
from __future__ import annotations
import time
from nextcord.ext import commands
from nextcord import Interaction, SlashOption, Embed
from cramesia_SS.utils.colors import normalize_hex, colour_from_hex
from cramesia_SS.constants import bot_colour
from cramesia_SS.views.helpview import HelpView   # ← single source

def setup(bot: commands.Bot):

    @bot.slash_command(name="ping", description="Ping the bot (response time).", force_global=True)
    async def ping(inter: Interaction):
        t0 = time.perf_counter()
        await inter.response.defer(ephemeral=True)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        await inter.followup.send(f"🏓 Pong! ~{dt_ms:.1f} ms")

    @bot.slash_command(name="color", description="Preview a HEX color.", force_global=True)
    async def color_cmd(
        inter: Interaction,
        hex: str = SlashOption(required=True, description="HEX like #AABBCC or AABBCC")
    ):
        norm = normalize_hex(hex)
        if not norm:
            return await inter.response.send_message("❌ Invalid HEX. Use a 6-digit HEX like `#RRGGBB`.", ephemeral=True)
        emb = Embed(title=f"Color Preview — {norm}",
                    description="This embed bar is set to your color.",
                    colour=colour_from_hex(norm))
        await inter.response.send_message(embed=emb)

    @bot.slash_command(name="help", description="See help topics", force_global=True)
    async def help_cmd(inter: Interaction):
        view = HelpView(inter.user.id, "quick")
        await inter.response.send_message(embed=view.cur_embed(), view=view, ephemeral=True)
        print("[help] HelpView loaded from:", HelpView.__module__)
