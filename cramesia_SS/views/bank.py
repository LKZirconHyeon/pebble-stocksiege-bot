from __future__ import annotations
from typing import List, Optional, Iterable

from datetime import datetime
import nextcord
from nextcord import Interaction, Embed
from nextcord.ui import View, button, Button

from cramesia_SS.constants import bot_colour


# ---------- small helpers ----------

def _render_page_lines(page: List[str] | str) -> str:
    """Allow pages to be either a prejoined string or a list of lines."""
    if isinstance(page, list):
        return "\n".join(str(x) for x in page)
    return str(page)

def _fmt_ts(ts: int | str | None) -> str:
    try:
        return datetime.fromtimestamp(int(ts or 0)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)

def _fmt_record(rec: dict) -> str:
    """
    Turn a raw history record into a readable line:
    2025-10-02 10:12  •  +3 → 42  •  Used R-hint.
    """
    change = int(rec.get("change", 0))
    sign = "+" if change > 0 else ""
    new_bal = int(rec.get("new_balance", 0))
    reason = str(rec.get("reason", "") or "—")
    return f"{_fmt_ts(rec.get('time'))}  •  {sign}{change} → {new_bal}  •  {reason}"

def format_history_pages(history: Iterable[dict] | None, per_page: int = 10) -> List[str]:
    """Human-friendly pages from raw history."""
    recs = list(history or [])
    recs.sort(key=lambda r: int(r.get("time", 0)), reverse=True)
    lines = [_fmt_record(r) for r in recs] or ["(no history yet)"]

    pages: List[str] = []
    for i in range(0, len(lines), per_page):
        pages.append("\n".join(lines[i:i + per_page]))
    return pages or ["(no history yet)"]


# ---------- pager view ----------

class BankBalanceViewer(View):
    """
    Minimal pager for hint-point balance + history, using pre-formatted pages.
    """
    def __init__(self, start_page_index: int, balance: int, pages: List[str], user: nextcord.abc.User):
        super().__init__(timeout=120)
        self.index = max(0, int(start_page_index))
        self.balance = int(balance)
        self.pages = pages or ["(no history yet)"]
        self.user_id = int(user.id)
        self.message: Optional[nextcord.Message] = None

    async def interaction_check(self, inter: Interaction) -> bool:
        # Only the invoker can drive the pager
        if inter.user.id != self.user_id:
            await inter.response.send_message("Only the original user can control this view.", ephemeral=True)
            return False
        return True

    def _update_buttons(self):
        self.prev_button.disabled = self.index <= 0
        self.next_button.disabled = self.index >= len(self.pages) - 1

    def cur_embed(self) -> Embed:
        page_total = len(self.pages)
        page_no = self.index + 1
        desc = _render_page_lines(self.pages[self.index])
        return Embed(
            title="Hint Points",
            description=(
                f"**Balance:** {self.balance}\n\n"
                f"**History (page {page_no}/{page_total})**\n{desc}"
            ),
            colour=bot_colour(),
        )

    @button(label="Prev", style=nextcord.ButtonStyle.secondary)
    async def prev_button(self, _btn: Button, inter: Interaction):
        if self.index > 0:
            self.index -= 1
        self._update_buttons()
        await inter.response.edit_message(embed=self.cur_embed(), view=self)

    @button(label="Next", style=nextcord.ButtonStyle.secondary)
    async def next_button(self, _btn: Button, inter: Interaction):
        if self.index < len(self.pages) - 1:
            self.index += 1
        self._update_buttons()
        await inter.response.edit_message(embed=self.cur_embed(), view=self)


def format_balance_embed(view: BankBalanceViewer) -> Embed:
    """Return the current embed for the provided view, with buttons in the right state."""
    view._update_buttons()
    return view.cur_embed()


__all__ = ["BankBalanceViewer", "format_balance_embed", "format_history_pages"]
