# cramesia_SS/game/mode_main/ac_others.py

from __future__ import annotations
from pathlib import Path
from typing import List, Tuple

import nextcord
from nextcord import Interaction, Embed
from nextcord.ui import View, button, Button
from nextcord.ext import commands

from cramesia_SS.db import db
from cramesia_SS.constants import (
    BOT_COLOUR,
    HELP_FILE_INFO,
    HELP_FILE_PLAYER,
    HELP_FILE_ADMIN,
    HELP_PAGE_LIMIT
)
from cramesia_SS.config import OWNER_ID
from cramesia_SS.utils.guards import guard, requires_mode, _mode_is  # same names as your utils.guards
from cramesia_SS.utils.time import now_ts as _now_ts

# If your HelpView + loader live in views/helpview.py (as we created earlier), import them:
from cramesia_SS.views.helpview import HelpView, load_help_pages as _load_help_pages

# ---------- tiny text helpers (identical behavior to the original) ----------
def _read_text_file(path: str) -> str | None:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


# ---------- elimination helpers (same signatures as in the old file) ----------
async def _current_result_year() -> int | None:
    """Read DB's last_result_year written after liquidate. None if not set."""
    cfg = await db.market.config.find_one({"_id": "current"}, {"last_result_year": 1})
    if not cfg:
        return None
    try:
        return int(cfg.get("last_result_year", 0)) or None
    except Exception:
        return None


async def _bottom_three_survivors() -> list[tuple[str, int]]:
    """
    Return bottom-3 (uid, cash) among NON-eliminated portfolios.
    Deterministic tie-break: cash asc, then _id asc.
    """
    survivors: List[Tuple[str, int]] = []
    async for pf in db.market.portfolios.find(
        {"$or": [{"eliminated": {"$exists": False}}, {"eliminated": False}]},
        {"cash": 1},
    ):
        survivors.append((pf["_id"], int(pf.get("cash", 0))))
    survivors.sort(key=lambda x: (x[1], x[0]))
    return survivors[:3]


async def _set_eliminated(user_id: str, year: int, *, cash: int | None = None, order: int | None = None) -> None:
    """Mark eliminated and store snapshot (cash/order) for fair ranking."""
    payload = {"eliminated": True, "elim_year": int(year), "updated_at": _now_ts()}
    if cash is not None:
        payload["elim_cash"] = int(cash)
    if order is not None:
        payload["elim_order"] = int(order)  # 1..3 within that round (1 = lowest cash)
    await db.market.portfolios.update_one({"_id": str(user_id)}, {"$set": payload}, upsert=False)


async def _get_elim_ranking_policy() -> str:
    doc = await db.market.config.find_one({"_id": "current"}, {"elim_ranking_policy": 1})
    pol = (doc or {}).get("elim_ranking_policy", "survival")
    return pol if pol in ("survival", "cash") else "survival"


async def _final_standings() -> list[tuple[str, int]]:
    """
    Compute final standings for elimination mode, honoring the configured policy:
    - 'survival': survivors outrank eliminated; ties by cash then _id
    - 'cash': everyone by final cash; ties by _id
    Returns a list of (uid, cash) sorted DESC for the top cash winners selection.
    """
    policy = await _get_elim_ranking_policy()
    pcol = db.market.portfolios

    # Pull necessary fields once
    rows: list[dict] = [pf async for pf in pcol.find({}, {"_id": 1, "cash": 1, "eliminated": 1})]
    if policy == "cash":
        rows.sort(key=lambda r: (int(r.get("cash", 0)), str(r["_id"])), reverse=True)
    else:  # survival
        # Survivors (eliminated=False/absent) first, then eliminated; each group by cash desc then _id
        def rank_key(r: dict):
            eliminated = bool(r.get("eliminated"))
            return (0 if not eliminated else 1, -int(r.get("cash", 0)), str(r["_id"]))
        rows.sort(key=rank_key)

    return [(str(r["_id"]), int(r.get("cash", 0))) for r in rows]


# =========================================================
# /elim_cut — OWNER, public, elimination-only (DB 5~10)
# =========================================================
@nextcord.slash_command(
    name="elim_cut",
    description="OWNER: Preview & confirm the 3 eliminations for this result (DB 5~10). Public.",
)
@guard(require_private=True, public=True, owner_only=True)
@requires_mode("elimination", public=True)
async def elimination_cut(interaction: Interaction):
    # Single public defer (old behavior)
    if not interaction.response.is_done():
        await interaction.response.defer()

    # Check result year window: DB 5~10 == 4th~9th result
    ry = await _current_result_year()
    if ry is None:
        await interaction.followup.send("❌ No `last_result_year` recorded yet. Run liquidation first.")
        return
    if not (5 <= ry <= 10):
        await interaction.followup.send(f"⛔ Eliminations run only for DB 5~10. Current DB={ry}.")
        return

    # Prevent duplicate cut for same year
    already = await db.market.portfolios.count_documents({"elim_year": int(ry)})
    if already > 0:
        await interaction.followup.send(f"⛔ Eliminations for DB {ry} already executed.")
        return

    # Select bottom 3 (preview)
    candidates = await _bottom_three_survivors()
    if len(candidates) < 3:
        await interaction.followup.send("❌ Not enough survivors to eliminate 3 players.")
        return

    lines = [f"- <@{uid}> — {cash}" for uid, cash in candidates]
    nth = ry - 1  # DB 5 == 4th result
    embed = Embed(
        title=f"Elimination Preview — DB {ry} (Result #{nth})",
        description=(
            "The following players are the **bottom 3 by unspent cash** and will be eliminated.\n"
            + "\n".join(lines)
            + "\n\nPress **Confirm Cut** to finalize.\n"
            "_Once executed, eliminated portfolios cannot buy/sell (admin override disabled)._"
        ),
        colour=BOT_COLOUR,
    )  # :contentReference[oaicite:0]{index=0}

    class ElimCutView(View):
        def __init__(self, owner_id: int, year: int, preview: list[tuple[str, int]]):
            super().__init__(timeout=600)
            self.owner_id = owner_id
            self.year = int(year)
            self.preview = preview

        async def interaction_check(self, btn_inter: Interaction) -> bool:
            if btn_inter.user.id != self.owner_id:
                await btn_inter.response.send_message("Owner only.", ephemeral=True)
                return False
            return True

        @button(label="Confirm Cut (3 players)", style=nextcord.ButtonStyle.danger)
        async def confirm(self, _btn: Button, btn_inter: Interaction):
            await btn_inter.response.defer()

            # Sanity checks again (mode/year/duplicate)
            if not await _mode_is("elimination"):
                await btn_inter.followup.send("⛔ Not in elimination mode anymore. Aborting.")
                self.disable_all_items()
                return

            ry2 = await _current_result_year()
            if ry2 != self.year:
                await btn_inter.followup.send(f"⛔ Result year changed (now DB {ry2}). Aborting.")
                self.disable_all_items()
                return

            already2 = await db.market.portfolios.count_documents({"elim_year": int(self.year)})
            if already2 > 0:
                await btn_inter.followup.send(f"⛔ Eliminations for DB {self.year} already executed.")
                self.disable_all_items()
                return

            # Recompute bottom 3 at commit time to avoid race
            current = await _bottom_three_survivors()
            if len(current) < 3:
                await btn_inter.followup.send("❌ Not enough survivors now. Aborting.")
                self.disable_all_items()
                return

            # Mark eliminated with snapshot (order = 1..3)
            for idx, (uid, cash) in enumerate(current, start=1):
                await _set_eliminated(uid, self.year, cash=cash, order=idx)

            self.disable_all_items()
            await btn_inter.followup.send(
                f"✅ Eliminations for **DB {self.year}** applied:\n" + "\n".join(f"- <@{u}> — {c}" for u, c in current)
            )

    view = ElimCutView(OWNER_ID, ry, candidates)
    msg = await interaction.followup.send(embed=embed, view=view)
    view.message = msg
    # (The structure follows the original preview+confirm flow.)  :contentReference[oaicite:1]{index=1}


# =========================================================
# /finalize — OWNER, public: declare the final winner (DB 11)
# =========================================================
@nextcord.slash_command(
    name="finalize",
    description="OWNER: Declare the final winner (requires DB 11 = after the 10th result). Public.",
)
@guard(require_private=True, public=True, owner_only=True)
async def finalize(interaction: Interaction):
    if not interaction.response.is_done():
        await interaction.response.defer()

    # Must be after the 10th result → DB 11
    ry = await _current_result_year()
    if ry != 11:
        await interaction.followup.send("⛔ Finalization is allowed only when **DB = 11**.")
        return

    standings = await _final_standings()
    if not standings:
        await interaction.followup.send("❌ No portfolios to rank.")
        return

    # Find max cash, collect all tied top players
    top_cash = max(c for _, c in standings)
    top_players = [(uid, c) for uid, c in standings if c == top_cash]

    if len(top_players) == 1:
        winner_uid, winner_cash = top_players[0]
        # (color name/hex lookup happens inside your signup helpers; if you split that elsewhere, import & call it)
        # To keep this self-contained without new imports, show basic winner info:
        embed = Embed(
            title="🏆 Final Winner Declared",
            description=(f"**Season complete (DB 11).**\n\n**Winner**: <@{winner_uid}>\n**Final Cash**: {winner_cash}"),
            colour=BOT_COLOUR,
        )
        await interaction.followup.send(embed=embed)
    else:
        lines = [f"- <@{uid}>" for uid, _ in top_players]
        embed = Embed(
            title="🏆 Final Winners (Tie)",
            description=(f"**Season complete (DB 11).**\n\n**Top Cash**: {top_cash}\n**Winners**:\n" + "\n".join(lines)),
            colour=BOT_COLOUR,
        )
        await interaction.followup.send(embed=embed)
    # (Matches the old winner/tie output.)  :contentReference[oaicite:2]{index=2}


# =========================================================
# /help — public, with section selector view (info/player/admin)
# =========================================================
@nextcord.slash_command(
    name="help",
    description="Show help (loads readme_info.txt / readme_player.txt / readme_admin.txt).",
)
@guard(require_private=False, public=True)
async def cmd_help(
    interaction: Interaction,
    section: str = nextcord.SlashOption(
        description="Which help to open.", required=False,
        choices={"Info": "info", "Player": "player", "Admin (owner only)": "admin"},
    ),
):
    if not interaction.response.is_done():
        await interaction.response.defer()

    requested = section or "info"
    if requested == "admin" and interaction.user.id != OWNER_ID:
        requested = "info"  # non-owner fallback

    pages, title = _load_help_pages(requested)
    view = HelpView(invoker_id=interaction.user.id, kind=requested, owner_id=OWNER_ID)

    if pages is None:
        missing = HELP_FILE_ADMIN if requested == "admin" else HELP_FILE_PLAYER if requested == "player" else HELP_FILE_INFO
        await interaction.followup.send(
            embed=Embed(title=title, description=f"❌ Missing file: `{missing}`", colour=BOT_COLOUR),
            view=view,
        )
        return

    view.pages, view.title = pages, title
    msg = await interaction.followup.send(embed=view.cur_embed(), view=view)  # cur_embed() should match your HelpView
    view.message = msg
    # (Behavior mirrors the original /help + HelpView.)  :contentReference[oaicite:3]{index=3}

def setup(bot: commands.Bot):
    # The three top-level slash commands defined above:
    bot.add_application_command(elimination_cut)
    bot.add_application_command(finalize)
    bot.add_application_command(cmd_help)