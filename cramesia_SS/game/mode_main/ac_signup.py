from __future__ import annotations

import re
from datetime import datetime

from nextcord.ext import commands
from nextcord import (
    Interaction, SlashOption, Embed, Member, AllowedMentions,
    ButtonStyle, Colour
)
from nextcord.ui import View, Button, Modal, TextInput

from cramesia_SS.db import db
from cramesia_SS.config import OWNER_ID, ALLOWED_SIGNUP_CHANNEL_ID
from cramesia_SS.constants import (
    COLOR_NAME_RE, ITEM_CODES, MAX_PLAYERS,
    STARTING_CASH, APOC_START_CASH, bot_colour
)
from cramesia_SS.utils.colors import normalize_hex, colour_from_hex
from cramesia_SS.utils.text import md_escape
from cramesia_SS.utils.time import now_ts
from cramesia_SS.utils.guards import guard


# ----- collection helpers ----------------------------------------------------

def _signups(): return db.players.signups
def _banks():   return db.hint_points.balance
def _ports():   return db.market.portfolios
def _cfg():     return db.market.config
def _signup_cfg(): return db.players.signup_settings  # single-doc store


# ----- tiny services mirrored from the monolith ------------------------------

async def _get_signup_settings() -> dict:
    doc = await _signup_cfg().find_one({"_id": "current"})
    if not doc:
        doc = {"_id": "current", "started": False, "locked_at": None}
        await _signup_cfg().insert_one(doc)
    return doc

async def _set_game_started(started: bool) -> None:
    await _signup_cfg().update_one(
        {"_id": "current"},
        {"$set": {"started": bool(started), "locked_at": now_ts() if started else None}},
        upsert=True
    )

async def _get_game_mode() -> str:
    doc = await _cfg().find_one({"_id": "current"}, {"game_mode": 1})
    mode = (doc or {}).get("game_mode", "classic")
    return mode if mode in ("classic", "apocalypse", "elimination") else "classic"


# ----- small utilities -------------------------------------------------------

async def _slots_left() -> int:
    total = await _signups().count_documents({})
    return max(0, MAX_PLAYERS - int(total))


# ===================== Cog: /signup =========================================

def setup(bot: commands.Bot):

    @bot.slash_command(name="signup", description="Player signup & roster tools", force_global=True)
    async def signup_root(inter: Interaction):
        pass

    # --- /signup join --------------------------------------------------------
    @signup_root.subcommand(
        name="join",
        description="Sign up and create your hint point inventory (0 pt).",
    )
    @guard(require_private=False, public=True)
    async def signup_join(
        inter: Interaction,
        color_name: str = SlashOption(required=True, max_length=20, description="Letters/spaces only, ≤20"),
        color_hex:  str = SlashOption(required=True, description="HEX like #AABBCC"),
    ):
        # channel restriction
        if ALLOWED_SIGNUP_CHANNEL_ID:
            ch = inter.channel
            in_allowed = (
                getattr(ch, "id", None) == ALLOWED_SIGNUP_CHANNEL_ID
                or getattr(ch, "parent_id", None) == ALLOWED_SIGNUP_CHANNEL_ID
            )
            if not in_allowed:
                return await inter.response.send_message(
                    f"❌ Use this in <#{ALLOWED_SIGNUP_CHANNEL_ID}>.", ephemeral=True
                )

        if not inter.response.is_done():
            await inter.response.defer()  # public

        # locked after start
        settings = await _get_signup_settings()
        if bool(settings.get("started")):
            return await inter.followup.send("🔒 Signups are locked — the game has already started.")

        # capacity
        if await _signups().count_documents({}) >= MAX_PLAYERS:
            return await inter.followup.send(f"❌ Capacity full: {MAX_PLAYERS}/{MAX_PLAYERS}. Signups are closed.")

        uid = str(inter.user.id)

        # duplicate signup
        if await _signups().find_one({"_id": uid}):
            return await inter.followup.send("❌ You have already signed up. You can only sign up once.")

        # orphan cleanup
        await _banks().delete_one({"_id": uid})
        await _ports().delete_one({"_id": uid})

        # validate inputs
        nm = color_name.strip()
        if not COLOR_NAME_RE.fullmatch(nm):
            return await inter.followup.send(
                "❌ Invalid color name. Use only English letters and spaces, up to 20 characters."
            )
        hex_norm = normalize_hex(color_hex)
        if not hex_norm:
            return await inter.followup.send("❌ Invalid HEX code. Provide a 6-digit HEX like `#RRGGBB`.")

        # uniqueness
        if await _signups().find_one({"color_name": {"$regex": f"^{re.escape(nm)}$", "$options": "i"}}):
            return await inter.followup.send("❌ This color name is already taken. Choose a different name.")
        if await _signups().find_one({"color_hex": {"$regex": f"^{re.escape(hex_norm)}$", "$options": "i"}}):
            return await inter.followup.send("❌ This HEX code is already used by another player.")

        # insert signup
        await _signups().insert_one({
            "_id": uid, "user_id": uid, "user_name": inter.user.name,
            "color_name": nm, "color_hex": hex_norm, "signup_time": now_ts(),
        })

        # create bank (0pt)
        await _banks().insert_one({
            "_id": uid,
            "balance": 0,
            "history": [{
                "time": now_ts(),
                "change": 0,
                "new_balance": 0,
                "user_id": str(bot.user.id) if bot.user else uid,
                "reason": "Signup - inventory opened (0 pt)",
            }],
        })

        # create portfolio (mode-aware start cash)
        mode = await _get_game_mode()
        start_cash = APOC_START_CASH if mode == "apocalypse" else STARTING_CASH
        await _ports().insert_one({
            "_id": uid, "user_id": uid, "cash": int(start_cash),
            "holdings": {c: 0 for c in ITEM_CODES}, "updated_at": now_ts(), "history": []
        })

        remain = await _slots_left()
        emb = Embed(
            title="✅ Signup Complete",
            description=(
                f"**Player**: {inter.user.mention}\n"
                f"**Color**: {nm} `{hex_norm}`\n"
                f"**Starting Cash**: {int(start_cash)}\n"
                f"**Slots left until closing**: **{remain}** / {MAX_PLAYERS}"
            ),
            colour=colour_from_hex(hex_norm),
        )
        await inter.followup.send(embed=emb)

    # --- /signup view (OWNER only) ------------------------------------------
    @signup_root.subcommand(
        name="view",
        description="View the roster: [User] — [Color Name] — [HEX]"
    )
    async def signup_view(inter: Interaction):
        if inter.user.id != OWNER_ID:
            await inter.response.defer(ephemeral=True)
            return await inter.followup.send("❌ Only the owner can run this command.")

        if not inter.response.is_done():
            await inter.response.defer()  # public

        lines = []
        cur = _signups().find(
            {}, projection={"user_id": 1, "color_name": 1, "color_hex": 1, "signup_time": 1}
        ).sort("signup_time", 1)

        async for d in cur:
            uid   = d.get("user_id") or d.get("_id")            # fallback to _id
            mention = f"<@{uid}>"
            cname = md_escape(d.get("color_name", "?"))
            chex  = md_escape(d.get("color_hex", "?"))
            lines.append(f"{mention} — {cname} — `{chex}`")

        roster = "\n".join(lines) if lines else "_No signups yet_"
        emb = Embed(
            title=f"Signup Roster ({len(lines)}/{MAX_PLAYERS})",
            description=roster,
            colour=bot_colour(),
        )
        await inter.followup.send(embed=emb)

    # --- /signup reset (interactive preview + apply) ------------------------
    @signup_root.subcommand(
        name="reset",
        description="Owner-only: Set NEW items first, then wipe signups/hint banks/portfolios and apply."
    )
    async def signup_reset(
        inter: Interaction,
        names: str = SlashOption(description="8 names separated by | (letters/spaces only, 1~20 chars each)", required=True),
        prices: str = SlashOption(description="8 integer prices separated by |", required=True),
        confirm: str = SlashOption(description="Type CONFIRM to prepare the preview.", required=True),
        mode: str = SlashOption(description="Game mode", required=False, choices=["classic", "apocalypse", "elimination"]),
    ):
        if inter.user.id != OWNER_ID:
            await inter.response.defer(ephemeral=True)
            return await inter.followup.send("❌ Only the owner can run this command.")

        await inter.response.defer(ephemeral=True)
        if confirm != "CONFIRM":
            return await inter.followup.send("❌ Type `CONFIRM` to prepare reset preview.")

        name_list = [s.strip() for s in names.split("|")]
        price_list = [s.strip() for s in prices.split("|")]
        if len(name_list) != 8 or len(price_list) != 8:
            return await inter.followup.send("❌ Provide exactly 8 names and 8 prices, separated by `|`.")

        NAME_RE = re.compile(r"^[A-Za-z ]{1,20}$")
        for i, nm in enumerate(name_list):
            if not NAME_RE.fullmatch(nm):
                return await inter.followup.send(f"❌ Invalid name for {ITEM_CODES[i]}: `{nm}` (letters/spaces, 1~20).")

        try:
            price_vals = [int(x) for x in price_list]
        except ValueError:
            return await inter.followup.send("❌ Prices must be integers.")
        if any(p <= 0 for p in price_vals):
            return await inter.followup.send("❌ All prices must be positive integers.")

        selected_mode = (mode or "classic").strip().lower()
        if selected_mode not in ("classic", "apocalypse", "elimination"):
            return await inter.followup.send("❌ Invalid game mode. Choose one of: classic, apocalypse, elimination.")

        base_items = {code: {"name": name_list[i], "price": price_vals[i]} for i, code in enumerate(ITEM_CODES)}
        applied_items = {c: dict(base_items[c]) for c in ITEM_CODES}
        if selected_mode == "apocalypse":
            for c in ITEM_CODES:
                applied_items[c]["price"] = int(applied_items[c]["price"]) * 100  # ×100 pricing

        base_preview  = "\n".join([f"{c}: **{base_items[c]['name']}** — {base_items[c]['price']}" for c in ITEM_CODES])
        final_preview = "\n".join([f"{c}: **{applied_items[c]['name']}** — {applied_items[c]['price']}" for c in ITEM_CODES])
        mode_note = {
            "classic":     "Classic mode. Prices are applied as-is.",
            "apocalypse":  "Apocalypse mode. **Starting cash = 1,000,000,000**. **All base prices are scaled ×100**.",
            "elimination": "Elimination mode. Uses standard pricing; elimination cuts are handled later.",
        }[selected_mode]

        class ResetConfirmView(View):
            def __init__(self, owner_id: int, base_items: dict, applied_items: dict, mode: str):
                super().__init__(timeout=600)
                self.owner_id = owner_id
                self.base_items = base_items
                self.applied_items = applied_items
                self.mode = mode
                self.message = None

            async def interaction_check(self, btn_inter: Interaction) -> bool:
                if btn_inter.user.id != self.owner_id:
                    await btn_inter.response.send_message("❌ Only the owner can confirm/cancel this.", ephemeral=True)
                    return False
                return True

        class ApplyButton(Button):
            def __init__(self):
                super().__init__(style=ButtonStyle.danger, label="Apply & Wipe", emoji="🗑️")

            async def callback(self, btn_inter: Interaction):
                await btn_inter.response.defer()
                try:
                    sres = await _signups().delete_many({})
                    bres = await _banks().delete_many({})
                    pres = await _ports().delete_many({})
                    cres = await db.stocks.changes.delete_many({})
                    try:
                        await db.stocks.prices.delete_many({})
                        await db.market.snapshots.delete_many({})
                    except Exception:
                        pass
                except Exception as e:
                    return await btn_inter.edit_original_message(content=f"❌ Error while wiping: {e}", view=None)

                try:
                    clean_items = {
                        code: {"name": applied_items[code]["name"], "price": int(applied_items[code]["price"])}
                        for code in ITEM_CODES
                    }
                    payload = {
                        "items": clean_items,
                        "game_mode": selected_mode,
                        "updated_at": now_ts(),
                        "last_result_year": 0,
                        "final_announced": False,
                        "final_winner": None,
                    }
                    if selected_mode == "apocalypse":
                        payload["apoc_start_cash"] = APOC_START_CASH

                    await _cfg().update_one(
                        {"_id": "current"},
                        {"$set": payload, "$unset": {"use_next_for_total": "", "next_year": ""}},
                        upsert=True
                    )
                    await _set_game_started(False)

                    lines = [f"{c}: **{clean_items[c]['name']}** — {clean_items[c]['price']}" for c in ITEM_CODES]
                    await btn_inter.edit_original_message(
                        embed=Embed(
                            title="✅ Reset Applied",
                            description=(
                                f"**Mode:** `{selected_mode}`\n{mode_note}\n\n"
                                "**Wiped collections**\n"
                                f"- Deleted signups: {sres.deleted_count}\n"
                                f"- Deleted hint banks: {bres.deleted_count}\n"
                                f"- Deleted portfolios: {pres.deleted_count}\n"
                                f"- Deleted stock changes: {cres.deleted_count}\n\n"
                                "**New Items (A~H)**\n" + "\n".join(lines)
                            ),
                            colour=bot_colour()
                        ),
                        view=None
                    )
                except Exception as e:
                    await btn_inter.edit_original_message(content=f"❌ Error while writing config: {e}", view=None)

        class CancelButton(Button):
            def __init__(self):
                super().__init__(style=ButtonStyle.secondary, label="Cancel", emoji="🚫")

            async def callback(self, btn_inter: Interaction):
                await btn_inter.response.edit_message(
                    embed=Embed(title="❎ Reset Cancelled", description="No data was deleted or changed.", colour=bot_colour()),
                    view=None
                )

        view = ResetConfirmView(OWNER_ID, base_items, applied_items, selected_mode)
        view.add_item(ApplyButton()); view.add_item(CancelButton())

        embed = Embed(
            title="⚠️ Reset Preview",
            description=(
                f"**Mode:** `{selected_mode}`\n{mode_note}\n\n"
                "If you press **Apply & Wipe**, the bot will:\n"
                "1) Delete **signups**, **hint point inventories**, **portfolios**, and **stock changes**.\n"
                "2) Replace **market items** with the following A~H config.\n\n"
                "**Input (Base) Prices**\n" + base_preview +
                "\n\n**Applied Prices**\n" + final_preview
            ),
            colour=bot_colour()
        )
        msg = await inter.followup.send(embed=embed, view=view)
        view.message = msg

    # --- /signup config (panel) ---------------------------------------------
    @signup_root.subcommand(name="config", description="Open the signup panel (self-config + admin tools).")
    async def signup_config(inter: Interaction):
        if not inter.response.is_done():
            await inter.response.defer()

        uid = str(inter.user.id)
        signup_doc = await _signups().find_one({"_id": uid})
        panel_colour = (
            colour_from_hex(signup_doc["color_hex"])
            if signup_doc and signup_doc.get("color_hex")
            else Colour.from_rgb(0, 0, 0)  # black for not-signed users
        )
        settings = await _get_signup_settings()
        mode = await _get_game_mode()

        MIN_START = 16 if mode in ("classic", "apocalypse") else MAX_PLAYERS
        cur_count = await _signups().count_documents({})
        slots_left = max(0, MAX_PLAYERS - cur_count)
        locked = bool(settings.get("started"))

        if signup_doc:
            summary = f"**You are signed up.**\nName: **{signup_doc['color_name']}**  •  HEX: `{signup_doc['color_hex']}`"
        else:
            summary = "You have **not** signed up yet. Use **/signup join** in the signup channel."

        lock_line  = "🔒 **Game Started** — signups & edits are locked." if locked else "🟢 Signups are **open**."
        slots_line = f"**Slots:** {cur_count} / {MAX_PLAYERS}" + (f"  •  ({slots_left} left)" if not locked else "")
        mode_line  = f"**Mode:** `{mode}`  •  **Min to start:** {MIN_START}"

        emb = Embed(title="Signup — Configuration",
                    description=f"{lock_line}\n{slots_line}\n{mode_line}\n\n{summary}",
                    colour=panel_colour)

        class Panel(View):
            def __init__(self, owner_id: int):
                super().__init__(timeout=600)
                self.panel_owner_id = int(owner_id)  # the user who opened this UI
                self.started = locked
                self.user_has_signup = signup_doc is not None

                # ── Help / “How do I sign up?” ──
                if not self.user_has_signup and not self.started:
                    btn_help = Button(style=ButtonStyle.primary, label="How do I sign up?")
                    btn_help.callback = self.on_help
                    self.add_item(btn_help)

                # ── Edit own color/hex ──
                btn_edit = Button(style=ButtonStyle.secondary, label="Edit My Color/HEX", emoji="🎨")
                btn_edit.callback = self.on_edit
                btn_edit.disabled = self.started or not self.user_has_signup
                self.add_item(btn_edit)

                # ── Start/Unlock button (OWNER ONLY, hidden for others) ──
                can_start = (not self.started) and (cur_count >= MIN_START)
                if inter.user.id == OWNER_ID:
                    label = "Start Game (Lock Signups)" if not self.started else "Unlock Signups"
                    emoji = "🚀" if not self.started else "🔓"
                    btn_start = Button(
                        style=ButtonStyle.success if not self.started else ButtonStyle.secondary,
                        label=label, emoji=emoji, disabled=(not self.started and not can_start)
                    )
                    btn_start.callback = self.on_toggle_start
                    self.add_item(btn_start)

                # ── Close (always shown) ──
                btn_close = Button(style=ButtonStyle.secondary, label="Close", emoji="❌")
                btn_close.callback = self.on_close
                self.add_item(btn_close)

            async def interaction_check(self, i: Interaction) -> bool:
                """Only panel owner or OWNER_ID may interact with this view."""
                if int(i.user.id) in (self.panel_owner_id, int(OWNER_ID)):
                    return True
                await i.response.send_message(
                    "❌ This menu isn’t yours. Use **/signup config** to open your own.",
                    ephemeral=True
                )
                return False

            async def on_help(self, i: Interaction):
                await i.response.send_message(
                    "Use **/signup join** in the designated signup channel to register.\n"
                    "You’ll choose your color name and HEX; capacity is limited.",
                    ephemeral=True
                )

            async def on_edit(self, i: Interaction):
                if i.user.id != self.panel_owner_id:
                    return await i.response.send_message("❌ Not your panel.", ephemeral=True)
                if self.started or not self.user_has_signup:
                    return await i.response.send_message("❌ Editing is locked.", ephemeral=True)

                class EditModal(Modal):
                    def __init__(self):
                        super().__init__("Edit Color / HEX")
                        self.color_name = TextInput(
                            label="Color Name (letters & spaces, 1~20)",
                            required=True, max_length=20,
                            default_value=signup_doc["color_name"] if signup_doc else ""
                        )
                        self.color_hex = TextInput(
                            label="HEX (e.g., #FF00AA or FF00AA)",
                            required=True,
                            default_value=signup_doc["color_hex"] if signup_doc else ""
                        )
                        self.add_item(self.color_name); self.add_item(self.color_hex)

                    async def callback(self, mi: Interaction):
                        name = self.color_name.value.strip()
                        hexv = self.color_hex.value.strip()
                        if not COLOR_NAME_RE.fullmatch(name):
                            return await mi.response.send_message(
                                "❌ Invalid color name. Use only English letters and spaces, up to 20 characters.",
                                ephemeral=True
                            )
                        norm = normalize_hex(hexv)
                        if not norm:
                            return await mi.response.send_message(
                                "❌ Invalid HEX code. Provide a 6-digit HEX like `#RRGGBB`.",
                                ephemeral=True
                            )
                        await _signups().update_one({"_id": uid}, {"$set": {
                            "color_name": name, "color_hex": norm,
                            "signup_time": signup_doc.get("signup_time") if signup_doc else now_ts()
                        }})
                        await mi.response.send_message(f"✅ Updated: **{name}** `{norm}`", ephemeral=False)

                await i.response.send_modal(EditModal())

            async def on_toggle_start(self, i: Interaction):
                if i.user.id != OWNER_ID:
                    return await i.response.send_message("❌ Owner only.", ephemeral=True)

                live = await _signups().count_documents({})
                if not self.started and live < MIN_START:
                    return await i.response.send_message(
                        f"⏳ Need at least **{MIN_START}** players to start "
                        f"(current: {live}/{MIN_START}).",
                        ephemeral=True
                    )

                new_state = not self.started
                await _set_game_started(new_state)
                self.started = new_state

                live_left = max(0, MAX_PLAYERS - live)
                new_lock  = "🔒 **Game Started** — signups & edits are locked." if new_state else "🟢 Signups are **open**."
                new_slots = f"**Slots:** {live} / {MAX_PLAYERS}" + (f"  •  ({live_left} left)" if not new_state else "")
                # Rebuild the panel (keeps owner-only Start/Unlock visibility)
                new_view = Panel(owner_id=self.panel_owner_id)
                await i.response.edit_message(
                    embed=Embed(
                        title="Signup — Configuration",
                        description=f"{new_lock}\n{new_slots}\n{mode_line}\n\n{summary}",
                        colour=bot_colour()
                    ),
                    view=new_view
                )

            async def on_close(self, i: Interaction):
                # Only the opener (or owner) can actually close the UI.
                if int(i.user.id) not in (self.panel_owner_id, int(OWNER_ID)):
                    return await i.response.send_message(
                        "❌ This menu isn’t yours; it remains open. Use **/signup config** to open your own.",
                        ephemeral=True
                    )
                for c in self.children: 
                    c.disabled = True
                await i.response.edit_message(view=None)

        # send panel **ephemeral** and bound to the opener
        await inter.followup.send(embed=emb, view=Panel(owner_id=inter.user.id), ephemeral=True)


    # --- /signup remove (OWNER only) ----------------------------------------
    @signup_root.subcommand(name="remove", description="OWNER: Remove a signed-up player and purge their data.")
    @guard(require_private=False, public=True, owner_only=True)
    async def signup_remove(
        inter: Interaction,
        user: Member = SlashOption(description="Player to remove", required=True),
        confirm: str = SlashOption(description='Type "CONFIRM" to proceed.', required=True),
    ):
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True)
        if confirm != "CONFIRM":
            return await inter.followup.send("❌ Type `CONFIRM` to proceed.")

        uid = str(user.id)
        deleted = 0
        deleted += (await _signups().delete_one({"_id": uid})).deleted_count
        deleted += (await _banks().delete_one({"_id": uid})).deleted_count
        deleted += (await _ports().delete_one({"_id": uid})).deleted_count

        await inter.followup.send(
            f"✅ Removed {user.mention} (deleted docs total: **{deleted}**).",
            allowed_mentions=AllowedMentions(everyone=False, roles=False, users=[]),
        )
