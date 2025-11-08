# cramesia_SS/game/mode_main/ac_stocks.py
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional
from decimal import Decimal, ROUND_HALF_UP

from nextcord.ext import commands
from nextcord import Interaction, SlashOption, Embed, ButtonStyle, ui
from nextcord.ui import View, Button

from cramesia_SS.db import db
from cramesia_SS.config import OWNER_ID
from cramesia_SS.constants import (
    ITEM_CODES, bot_colour, ODDS, ODDS_APOC,
)
from cramesia_SS.utils.guards import guard, requires_mode
from cramesia_SS.utils.time import now_ts
from cramesia_SS.utils.text import round_half_up_int, fmt_price
from cramesia_SS.services.market_math import calculate_odds
from cramesia_SS.services.snapshots import snapshot_pre_reveal, snapshot_liquidate  # both exist in your services
from cramesia_SS.services.generator import generate_preview_or_commit, build_preview_embed, commit_preview, compute_rhint_odds, compute_owner_odds

# ---- collection helpers -----------------------------------------------------
def _cfg():      # singleton config: {"_id":"current", items, use_next_for_total?, next_year?, game_mode? ...}
    return db.market.config
def _changes():  # yearly % changes
    return db.stocks.changes
def _ports():    # player portfolios
    return db.market.portfolios
def _snapshots():   # liquidation / pre-reveal snapshots live here
    return db.market.snapshots

# ---- small utils lifted from the monolith -----------------------------------
async def _get_changes_for_year(year: int) -> dict | None:
    """Return {A..H: percent} for a year, or None."""
    doc = await _changes().find_one({"_id": int(year)})
    if not doc:
        return None
    return {k: int(v) for k, v in doc.items() if k in ITEM_CODES}

def _price_with_change(base_price: int, percent: int) -> int:
    """100% => 2x; -50% => 0.5x; round to int; never negative."""
    return max(0, round_half_up_int(base_price * (100 + percent) / 100.0))

async def _get_market_config() -> dict | None:
    return await _cfg().find_one({"_id": "current"})

def _item_label(code: str, items_cfg: dict) -> str:
    info = (items_cfg or {}).get(code, {})
    return f"{code} — {info.get('name', code)}"

async def _mode() -> str:
    cfg = await _get_market_config() or {}
    return (cfg.get("game_mode") or "classic").lower()

def round_half_up_int(x: float | int) -> int:
    """0–4 down, 5–9 up (classic half-up)."""
    return int(Decimal(x).quantize(0, rounding=ROUND_HALF_UP))

def fmt_price(n: int | float) -> str:
    """Half-up round then thousands separators for display."""
    return f"{round_half_up_int(n):,}"

# ---- elimination helpers ----------------------------------------------------
async def _current_result_year() -> int | None:
    cfg = await _cfg().find_one({"_id": "current"}, {"last_result_year": 1})
    if not cfg:
        return None
    try:
        return int(cfg.get("last_result_year", 0)) or None
    except Exception:
        return None

async def _bottom_three_survivors() -> list[tuple[str, int]]:
    """Return bottom-3 (uid, cash) among NON-eliminated portfolios."""
    survivors: list[tuple[str, int]] = []
    async for pf in _ports().find(
        {"$or": [{"eliminated": {"$exists": False}}, {"eliminated": False}]},
        {"cash": 1},
    ):
        survivors.append((str(pf["_id"]), int(pf.get("cash", 0))))
    survivors.sort(key=lambda x: (x[1], x[0]))  # cash asc, then id asc (stable)
    return survivors[:3]

async def _set_eliminated(user_id: str, year: int, *, cash: int | None = None, order: int | None = None) -> None:
    payload = {"eliminated": True, "elim_year": int(year), "updated_at": now_ts()}
    if cash is not None:
        payload["elim_cash"] = int(cash)
    if order is not None:
        payload["elim_order"] = int(order)  # 1..3 within that round
    await _ports().update_one({"_id": str(user_id)}, {"$set": payload})

async def _get_elim_ranking_policy() -> str:
    doc = await _cfg().find_one({"_id": "current"}, {"elim_ranking_policy": 1})
    pol = (doc or {}).get("elim_ranking_policy", "survival")
    return pol if pol in ("survival", "cash") else "survival"

async def _final_standings() -> list[tuple[str, int]]:
    """Return (uid, cash) in final ranking order based on policy."""
    policy = await _get_elim_ranking_policy()
    rows = [pf async for pf in _ports().find({}, {"_id": 1, "cash": 1, "eliminated": 1})]
    if policy == "cash":
        rows.sort(key=lambda r: (int(r.get("cash", 0)), str(r["_id"])), reverse=True)
    else:
        # survivors ranked ahead of eliminated; then higher cash first
        def key(r: dict):
            return (0 if not bool(r.get("eliminated")) else 1, -int(r.get("cash", 0)), str(r["_id"]))
        rows.sort(key=key)
    return [(str(r["_id"]), int(r.get("cash", 0))) for r in rows]

# ============================= Cog ===========================================

def setup(bot: commands.Bot):

    @bot.slash_command(
        name="stock_change",
        description="Owner: manage yearly stock % changes and reveal next-year projection",
        force_global=True,
    )
    async def stock_change_cmd(inter: Interaction):
        pass  # group root

    # ---------- NEW: /stock_change generate ---------------------------------
    @stock_change_cmd.subcommand(
        name="generate",
        description="OWNER: Generate 8 random changes with ETU Preview, then confirm."
    )
    @guard(require_private=False, public=True, owner_only=True)
    async def stock_change_generate(inter: Interaction):
        """
        No options version.
        - Year is auto-determined internally (2..11).
        - RNG seed is internal; no manual override.
        - No commit notes or override flags.
        """
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True)

        # Generate preview (dry-run)
        try:
            preview = await generate_preview_or_commit(
                year=None,      # auto 2..11
                dry_run=True    # preview mode
            )
        except Exception as e:
            return await inter.followup.send(f"❌ Generate failed: {e}", ephemeral=True)

        class GenerateView(View):
            """
            Buttons:
            - Confirm & Save (locks the season entry)
            - Re-roll (re-generate preview with same auto rules)
            - Cancel (close the prompt)
            """
            def __init__(self, params: dict, doc: dict):
                super().__init__(timeout=180)
                self.params = params      # {'year': None, 'dry_run': True/False}
                self.doc = doc            # preview document

            async def interaction_check(self, btn_inter: Interaction) -> bool:
                # Only the invoker (owner) can press buttons
                if btn_inter.user.id != inter.user.id:
                    await btn_inter.response.send_message("Owner only.", ephemeral=True)
                    return False
                return True

            async def _render(self) -> Embed:
                return build_preview_embed(self.doc)

            @ui.button(label="✅ Confirm & Save (Lock)", style=ButtonStyle.success)
            async def confirm(self, button: ui.Button, btn_inter: Interaction):
                await btn_inter.response.defer()
                try:
                    # 🔒 Commit exactly what is in self.doc (no RNG rerun)
                    saved = await commit_preview(self.doc)
                except Exception as e:
                    return await btn_inter.followup.send(f"❌ Save failed: {e}", ephemeral=True)
        
                # Remove the view and finalize
                await btn_inter.edit_original_message(
                    content=f"✅ Saved & locked — Year **{saved['year']}**",
                    embed=None, view=None
                )
                self.stop()

            @ui.button(label="🎲 Re-roll", style=ButtonStyle.secondary)
            async def reroll(self, _btn: Button, btn_inter: Interaction):
                await btn_inter.response.defer()
                # Re-generate preview
                self.params["dry_run"] = True
                self.doc = await generate_preview_or_commit(**self.params)
                e = await self._render()
                await btn_inter.edit_original_message(embed=e, view=self)

            @ui.button(label="✖ Cancel", style=ButtonStyle.danger)
            async def cancel(self, button: ui.Button, btn_inter: Interaction):
                # First response must edit the original component message
                try:
                    await btn_inter.response.edit_message(content="Canceled.", embed=None, view=None)
                except Exception:
                    await btn_inter.edit_original_message(content="Canceled.", embed=None, view=None)
                self.stop()

        # Params now contain only what the service expects
        params = dict(year=None, dry_run=True)
        view = GenerateView(params, preview)
        embed = build_preview_embed(preview)
        await inter.followup.send(embed=embed, view=view, ephemeral=True)


    # ---------- /stock_change odds  -----------------------------------
    @stock_change_cmd.subcommand(
        name="odds",
        description="Owner: show R-hint (n-1) and Owner odds (n) from locked (DB) history."
    )
    @guard(require_private=False, public=True, owner_only=True)
    async def stock_change_odds(inter: Interaction):
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True)
    
        # 0) 항목 이름표
        cfg = await _cfg().find_one({"_id": "current"}, {"items": 1})
        items_cfg = (cfg or {}).get("items", {})
    
        # 1) 최신 DB(locked) 기준 연도 계산  ← lry(정산) 말고!
        locked_docs = [d async for d in _changes().find({"locked": True}, {"_id": 1})]
        locked_docs.sort(key=lambda d: int(d["_id"]))
        ldb = max([int(x["_id"]) for x in locked_docs], default=0)     # n = 최신 locked 연도
        locked_count = len(locked_docs)
    
        def _lbl_n()   -> str: return f"Year {ldb}"   if ldb >= 1 else "—"
        def _lbl_nm1() -> str: return f"Year {ldb-1}" if ldb >= 2 else "—"
    
        # 2) 계산은 services.generator 쪽 헬퍼(이미 DB 기준) 사용
        r_map = await compute_rhint_odds()   # n-1 계산 (최신 locked 제외)
        o_map = await compute_owner_odds()   # n 계산   (n-1 + 최신 locked 보정)
    
        # 3) 출력 (히스토리 유무 판단도 locked 기준)
        if locked_count < 2:
            r_text = f"R-hint (n-1 = {_lbl_nm1()})\n\nNo historical data yet."
        else:
            r_lines = [f"{_item_label(code, items_cfg)}: {int(r_map.get(code, 50))}%"
                       for code in ITEM_CODES]
            r_text = f"R-hint (n-1 = {_lbl_nm1()})\n\n" + "\n".join(r_lines)
    
        o_lines = [f"{_item_label(code, items_cfg)}: {int(o_map.get(code, 50))}%"
                   for code in ITEM_CODES]
        o_text = f"Owner odds (n = {_lbl_n()})\n\n" + "\n".join(o_lines)
    
        await inter.followup.send(f"{r_text}\n\n{o_text}")
    

    # ---------- /stock_change reveal_next ----------------------------------
    @stock_change_cmd.subcommand(
        name="reveal_next",
        description="Owner: Reveal ONLY the immediate next year's prices (sequential); switches totals to NEXT.",
    )
    @guard(require_private=False, public=True, owner_only=True)
    async def stock_change_reveal_next(
        inter: Interaction,
        confirm: str = SlashOption(description="Type CONFIRM to proceed.", required=True),
    ):
        from datetime import datetime

        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True)

        if confirm != "CONFIRM":
            return await inter.followup.send("❌ Type `CONFIRM` to proceed.")

        cfg_col = _cfg()
        cfg = await cfg_col.find_one({"_id": "current"}) or {}
        items = (cfg.get("items") or {})
        if not items:
            return await inter.followup.send("❌ Market is not configured.")

        if cfg.get("use_next_for_total"):
            ny = cfg.get("next_year")
            return await inter.followup.send(
                f"❌ NEXT totals already active for **{ny}**. "
                "Run `/stock_change liquidate` (or revert) before revealing again."
            )

        last_result_year = int(cfg.get("last_result_year") or 1)
        expected_year = last_result_year + 1

        ch = await _get_changes_for_year(expected_year)
        if not ch:
            return await inter.followup.send(
                f"❌ No changes found for **{expected_year}**. "
                f"Set them first with `/stock_change set year:{expected_year}` (or `/stock_change generate`)."
            )

        await snapshot_pre_reveal(int(expected_year))

        preview_lines = []
        for code in ITEM_CODES:
            info = (items.get(code) or {})
            base_price = int(info.get("price", 0))
            pct = int(ch.get(code, 0))
            new_next = _price_with_change(base_price, pct)
            info["next_price"] = new_next
            items[code] = info

            preview_lines.append(
                f"{code}: {info.get('name', code)} — "
                f"{fmt_price(base_price)} → **{fmt_price(new_next)}** ({pct:+d}%)"
            )

        await cfg_col.update_one(
            {"_id": "current"},
            {"$set": {
                "items": items,
                "use_next_for_total": True,
                "next_year": int(expected_year),
                "updated_at": int(datetime.now().timestamp()),
            }},
            upsert=True,
        )

        await inter.followup.send(
            embed=Embed(
                title=f"Next-Year Revealed — {expected_year}",
                description="\n".join(preview_lines),
                colour=bot_colour(),
            )
        )


    # ---------- /stock_change view ------------------------------------
        
    @stock_change_cmd.subcommand(
        name="view",
        description="Owner: view the 8 set percentages (A–H) for a given year."
    )
    @guard(require_private=False, public=False, owner_only=True)
    async def stock_change_view(
        inter: Interaction,
        year: int = SlashOption(description="Year to view", required=True),
    ):
        # behavior & validation as in monolith
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True)

        doc = await _changes().find_one({"_id": int(year)})
        if not doc:
            return await inter.followup.send(f"❌ No changes found for {year}.")
        lines = [f"{c}: {int(doc.get(c, 0))}%" for c in ITEM_CODES]
        await inter.followup.send(
            embed=Embed(
                title=f"Year {year} — Set Changes",
                description="\n".join(lines),
                colour=bot_colour(),
            )
        )

    # ---------- /stock_change liquidate ------------------------------------
    @stock_change_cmd.subcommand(
        name="liquidate",
        description="Owner: liquidate holdings into Unspent at the currently shown prices.",
    )
    @guard(require_private=False, public=True, owner_only=True)
    async def stock_change_liquidate(
        inter: Interaction,
        confirm: str = SlashOption(description="Type CONFIRM to proceed.", required=True),
    ):
        # behavior & validation as in monolith
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True)

        if confirm != "CONFIRM":
            return await inter.followup.send("❌ Type `CONFIRM` to proceed.")

        cfg_col = _cfg()
        cfg = await cfg_col.find_one({"_id": "current"})
        if not cfg or "items" not in cfg:
            return await inter.followup.send("❌ Market is not configured.")

        items = cfg["items"]
        use_next = bool(cfg.get("use_next_for_total"))
        result_year = int(cfg.get("next_year") or cfg.get("last_result_year") or 0)

        # 1) Promote latest pre_reveal snapshot -> the single 'revert' snapshot
        try:
            # your historical snapshots use `taken_at`; be flexible
            latest_pre = await _snapshots().find_one(
                {"type": "pre_reveal"},
                sort=[("taken_at", -1), ("created_at", -1)]
            )
            if latest_pre:
                doc = {k: v for k, v in latest_pre.items() if k != "_id"}
                doc["type"] = "revert"
                # write the timestamp in the same style your DB already has
                doc["taken_at"] = now_ts()
                doc.pop("created_at", None)
                await _snapshots().insert_one(doc)
        
                # keep only the newest 'revert'
                ids = [d["_id"] async for d in _snapshots().find(
                    {"type": "revert"},
                    sort=[("taken_at", -1), ("created_at", -1)],
                    projection={"_id": 1}
                )]
                for old_id in ids[1:]:
                    await _snapshots().delete_one({"_id": old_id})
        except Exception:
            # snapshotting must not block liquidation
            pass

        # 2) Liquidate portfolios at the SHOWN price
        count = 0
        async for pf in _ports().find({}):
            uid = pf["_id"]
            holdings = dict(pf.get("holdings") or {})
            cash = int(pf.get("cash", 0))
            gain = 0
            for code, qty in holdings.items():
                q = int(qty or 0)
                if q <= 0:
                    continue
                show_px = int(items[code]["next_price"]) if use_next else int(items[code]["price"])
                gain += q * show_px
                holdings[code] = 0
            if gain:
                await _ports().update_one(
                    {"_id": uid},
                    {"$set": {"cash": cash + gain, "holdings": holdings, "updated_at": now_ts()},
                     "$push": {"history": {"t": now_ts(), "type": "liquidate", "amount": gain}}}
                )
                count += 1

        # 4) If NEXT was visible, commit it to current and clear flags
        if use_next:
            for c, it in list(items.items()):
                np = it.get("next_price")
                if np is not None:
                    it["price"] = int(np)
                    it.pop("next_price", None)
            await cfg_col.update_one(
                {"_id": "current"},
                {"$set": {
                    "items": items,
                    "use_next_for_total": False,
                    "last_result_year": int(cfg.get("next_year") or cfg.get("last_result_year") or 0),
                    "updated_at": now_ts(),
                },
                 "$unset": {"next_year": ""}}
            )

        await inter.followup.send(f"✅ Liquidation complete for **{count}** portfolios.")

    # ---------- /stock_change revert -------------------------------------------
    @stock_change_cmd.subcommand(
        name="revert",
        description="Owner: revert to the latest pre-reveal snapshot (or the single 'revert' copy)."
    )
    @guard(require_private=False, public=True, owner_only=True)
    async def stock_change_revert(
        inter: Interaction,
        confirm: str = SlashOption(description="Type REVERT to proceed.", required=True),
    ):
        # behavior & validation as in monolith
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True)

        if confirm != "REVERT":
            return await inter.followup.send("❌ Type `REVERT` to proceed.")

        snaps = _snapshots()

        snap = None
        cursor = snaps.find({})
        async for doc in cursor:
            snap = doc
            break
        if not snap:
            return await inter.followup.send("❌ No snapshot found to revert to.")

        # ----- restore config (strip any next_price and disable NEXT mode)
        cfg_src = snap.get("config") or {}
        items = (cfg_src.get("items") or snap.get("items") or {})
        for code, it in list(items.items()):
            if isinstance(it, dict) and "next_price" in it:
                it.pop("next_price", None)

        await _cfg().update_one(
            {"_id": "current"},
            {"$set": {"items": items, "use_next_for_total": False, "updated_at": now_ts()},
             "$unset": {"next_year": ""}},
            upsert=True
        )

        # ----- restore portfolios
        restored = 0
        for p in snap.get("portfolios", []):
            doc = {
                "_id": p["_id"],
                "cash": int(p.get("cash", 0)),
                "holdings": dict(p.get("holdings") or {}),
                "updated_at": now_ts(),
            }
            if p.get("frozen_year") is not None:
                doc["frozen_year"] = p["frozen_year"]
            await _ports().replace_one({"_id": p["_id"]}, doc, upsert=True)
            restored += 1

        await inter.followup.send(
            f"↩️ Reverted to snapshot.\n"
            f"- Restored portfolios: **{restored}**\n"
            f"- NEXT pricing disabled; `next_year` cleared; `next_price` removed."
        )
    # ---------- /stock_change elim_cut -------------------------------------------
    @stock_change_cmd.subcommand(
        name="elim_cut",
        description="OWNER: Preview & confirm the 3 eliminations for this result (DB 5~10)."
    )
    @requires_mode("elimination", public=True)
    @guard(require_private=False, public=True, owner_only=True)
    async def stock_change_elim_cut(inter: Interaction):
        
        # behavior & validation as in monolith
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True)

        ry = await _current_result_year()
        if ry is None:
            return await inter.followup.send("❌ No `last_result_year` recorded yet. Run liquidation first.")
        if not (5 <= ry <= 10):
            return await inter.followup.send(f"⛔ Eliminations run only for DB 5~10. Current DB={ry}.")
        if await _ports().count_documents({"elim_year": int(ry)}) > 0:
            return await inter.followup.send(f"⛔ Eliminations for DB {ry} already executed.")

        candidates = await _bottom_three_survivors()
        if len(candidates) < 3:
            return await inter.followup.send("❌ Not enough survivors to eliminate 3 players.")

        lines = [f"- <@{uid}> — {cash}" for uid, cash in candidates]
        nth = ry - 1
        emb = Embed(
            title=f"Elimination Preview — DB {ry} (Result #{nth})",
            description=("The following players are the **bottom 3 by unspent cash** and will be eliminated.\n"
                         + "\n".join(lines)
                         + "\n\nPress **Confirm Cut** to finalize.\n"
                         "_Once executed, eliminated portfolios cannot buy/sell (admin override disabled)._"),
            colour=bot_colour(),
        )

        class ElimCutView(View):
            def __init__(self, owner_id: int, year: int):
                super().__init__(timeout=600)
                self.owner_id = owner_id
                self.year = int(year)

            async def interaction_check(self, btn_inter: Interaction) -> bool:
                if btn_inter.user.id != self.owner_id:
                    await btn_inter.response.send_message("Owner only.", ephemeral=True)
                    return False
                return True

            @ui.button(label="Confirm Cut (3 players)", style=ButtonStyle.danger)
            async def confirm(self, _btn: Button, btn_inter: Interaction):
                await btn_inter.response.defer()
                # re-validate
                cur_year = await _current_result_year()
                if cur_year != self.year:
                    return await btn_inter.followup.send("⛔ Result year changed. Aborting.")
                if await _ports().count_documents({"elim_year": int(self.year)}) > 0:
                    return await btn_inter.followup.send(f"⛔ Eliminations for DB {self.year} already executed.")

                current = await _bottom_three_survivors()
                if len(current) < 3:
                    return await btn_inter.followup.send("❌ Not enough survivors now. Aborting.")

                for idx, (uid, cash) in enumerate(current, start=1):
                    await _set_eliminated(uid, self.year, cash=cash, order=idx)

                self.disable_all_items()
                await btn_inter.edit_original_message(view=self)
                await btn_inter.followup.send(
                    f"✅ Eliminations for **DB {self.year}** applied:\n" +
                    "\n".join(f"- <@{u}> — {c}" for u, c in current)
                )

        view = ElimCutView(OWNER_ID, ry)
        msg = await inter.followup.send(embed=emb, view=view)
        view.message = msg

    # ---------- /stock_change finalize -------------------------------------------
    @stock_change_cmd.subcommand(
        name="finalize",
        description="OWNER: Declare the final winner (requires DB 11 = after the 10th result)."
    )
    @guard(require_private=False, public=True, owner_only=True)
    async def stock_change_finalize(inter: Interaction):
        
        # behavior & validation as in monolith
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True)

        ry = await _current_result_year()
        if ry != 11:
            return await inter.followup.send("⛔ Finalization is allowed only when **DB = 11**.")

        standings = await _final_standings()
        if not standings:
            return await inter.followup.send("❌ No portfolios to rank.")

        top_cash = max(c for _, c in standings)
        top_players = [(uid, c) for uid, c in standings if c == top_cash]

        if len(top_players) == 1:
            winner_uid, winner_cash = top_players[0]
            emb = Embed(
                title="🏆 Final Winner Declared",
                description=f"**Season complete (DB 11).**\n\n**Winner**: <@{winner_uid}>\n**Final Cash**: {winner_cash}",
                colour=bot_colour(),
            )
        else:
            lines = [f"- <@{uid}>" for uid, _ in top_players]
            emb = Embed(
                title="🏆 Final Winners (Tie)",
                description=f"**Season complete (DB 11).**\n\n**Top Cash**: {top_cash}\n**Winners**:\n" + "\n".join(lines),
                colour=bot_colour(),
            )

        await inter.followup.send(embed=emb)
