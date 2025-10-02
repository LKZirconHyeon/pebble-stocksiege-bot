# cramesia_SS/views/helpview.py
from __future__ import annotations
from pathlib import Path
from typing import List, Optional, Tuple

import nextcord
from nextcord import Embed, Interaction
from nextcord.ui import View, button, Button

from cramesia_SS.constants import bot_colour, HELP_PAGE_LIMIT

HELP_DIR = Path(__file__).resolve().parents[2] / "help_data"

SECTIONS = {
    "quick":       ("Quick",        "h_quick.txt"),
    "quick help":  ("Quick Help",   "h_quick.txt"),
    "signup":      ("Signup",       "h_signup.txt"),
    "market":      ("Market",       "h_market.txt"),
    "stocks":      ("Stocks",       "h_stocks.txt"),
    "hint points": ("Hint Points",  "h_hint_points.txt"),
    "use hints":   ("Use Hints",    "h_use_hints.txt"),
    "fun":         ("Fun",          "h_fun.txt"),
}

def load_help_text(file_name: str) -> str:
    try:
        return (HELP_DIR / file_name).read_text(encoding="utf-8")
    except Exception as e:
        return f"⚠ Could not load help file: {file_name}\nError: {e}"

def split_help_text(text: str, limit: int = HELP_PAGE_LIMIT) -> List[str]:
    if not text:
        return ["(empty)"]
    out, cur, cur_len = [], [], 0

    def flush():
        nonlocal cur, cur_len
        if cur:
            out.append("\n".join(cur))
            cur, cur_len = [], 0

    for line in text.splitlines():
        line = line.rstrip()
        needed = len(line) + (1 if cur else 0)
        if cur_len + needed <= limit:
            cur.append(line)
            cur_len += needed
        else:
            flush()
            while len(line) > limit:
                out.append(line[:limit])
                line = line[limit:]
            if line:
                cur.append(line)
                cur_len = len(line)

    flush()
    return out or ["(empty)"]

def load_section_pages(section: str) -> Tuple[List[str], str]:
    key = (section or "").strip().lower()
    meta = SECTIONS.get(key) or SECTIONS["signup"]  # default to Signup
    title, filename = meta
    text = load_help_text(filename)
    return split_help_text(text), title

class HelpView(View):
    """
    Help panel with only section buttons (no prev/next).
    """
    def __init__(self, invoker_id: int, section: str = "quick"):
        super().__init__(timeout=300)
        self.invoker_id = int(invoker_id)
        self.pages: List[str] = []
        self.index = 0
        self.title = ""
        self.set_section(section)

    def set_section(self, section: str) -> None:
        self.pages, base = load_section_pages(section)
        self.title = base
        self.index = 0
        self._sync_buttons()  # no-op now

    def cur_embed(self) -> Embed:
        body = self.pages[self.index] if self.pages else "(no content)"
        page_no = self.index + 1 if self.pages else 0
        page_total = len(self.pages) if self.pages else 0
        return Embed(
            title=f"{self.title} ({page_no}/{page_total})" if page_total else self.title,
            description=body,
            colour=bot_colour(),
        )

    async def interaction_check(self, inter: Interaction) -> bool:
        if inter.user.id != self.invoker_id:
            await inter.response.send_message("This help panel isn’t yours.", ephemeral=True)
            return False
        return True

    def _sync_buttons(self):
        # Prev/Next removed; keep method for compatibility/no-ops.
        return

    # ---- section buttons (edit the same message) ----
    @button(label="Quick Help", style=nextcord.ButtonStyle.secondary, row=0)
    async def sec_quick(self, _btn: Button, inter: Interaction):
        self.set_section("quick")
        await inter.response.edit_message(embed=self.cur_embed(), view=self)

    @button(label="Signup", style=nextcord.ButtonStyle.secondary, row=0)
    async def sec_signup(self, _btn: Button, inter: Interaction):
        self.set_section("signup")
        await inter.response.edit_message(embed=self.cur_embed(), view=self)

    @button(label="Market", style=nextcord.ButtonStyle.secondary, row=0)
    async def sec_market(self, _btn: Button, inter: Interaction):
        self.set_section("market")
        await inter.response.edit_message(embed=self.cur_embed(), view=self)

    @button(label="Stocks", style=nextcord.ButtonStyle.secondary, row=0)
    async def sec_stocks(self, _btn: Button, inter: Interaction):
        self.set_section("stocks")
        await inter.response.edit_message(embed=self.cur_embed(), view=self)

    @button(label="Hint Points", style=nextcord.ButtonStyle.secondary, row=1)
    async def sec_hint_points(self, _btn: Button, inter: Interaction):
        self.set_section("hint points")
        await inter.response.edit_message(embed=self.cur_embed(), view=self)

    @button(label="Use Hints", style=nextcord.ButtonStyle.secondary, row=1)
    async def sec_use_hints(self, _btn: Button, inter: Interaction):
        self.set_section("use hints")
        await inter.response.edit_message(embed=self.cur_embed(), view=self)

    @button(label="Fun", style=nextcord.ButtonStyle.secondary, row=1)
    async def sec_fun(self, _btn: Button, inter: Interaction):
        self.set_section("fun")
        await inter.response.edit_message(embed=self.cur_embed(), view=self)

__all__ = ["HelpView", "load_section_pages", "split_help_text", "load_help_text", "HELP_FILES"]
