from motor.motor_asyncio import AsyncIOMotorClient  # MongoDB library
import nextcord  # Discord bot library
from nextcord.ext import commands
from nextcord import Interaction, SlashOption, SelectOption, ButtonStyle, Embed, Member, AllowedMentions, Colour, Message
from nextcord.ui import View, Button, button, Modal, TextInput, Select
from datetime import datetime
from dotenv import load_dotenv
from pathlib import Path
import os
import re
import copy
import functools

load_dotenv()
db_client = AsyncIOMotorClient(os.getenv("DB_URL"))  # put link to database, MongoDB has free cloud service
bot = commands.Bot(intents=nextcord.Intents(guilds=True, members=True, message_content=True, messages=True))

# Basic Setup
OWNER_ID = int(os.getenv("OWNER_ID"))
ODDS = {-80: 20, -75: 18, -70: 15, -60: 12, -50: 10, -45: 9, -40: 8, -35: 7, -30: 6, -25: 5, -20: 4, -15: 3, -10: 2, -5: 1, 0: 0, 5: -1, 10: -1, 15: -2, 20: -2, 25: -3, 30: -3, 40: -4, 50: -5, 60: -6, 70: -7, 80: -8, 90: -9, 100: -10, 150: -12, 200: -15, 300: -18, 400: -20}
ODDS_APOC = {0: -20, -5: -15, -10: -12, -15: -9, -20: -6, -25: -3, -30: 0, -40: 3, -50: 6, -60: 9, -70: 12, -75: 15, -80: 20}
NORMAL_STOCK_CHANGES = tuple(ODDS.keys())
BOT_COLOUR = Colour.from_rgb(169, 46, 33)
MAX_PLAYERS = 24

# Signup Setup
COLOR_NAME_RE = re.compile(r'^[A-Za-z ]{3,20}$')
HEX_RE = re.compile(r'^#?(?:[0-9a-fA-F]{6})$')

# Numeric & Item Setup
STARTING_CASH = 500_000
MAX_ITEM_UNITS = 9_999_999
APOC_START_CASH = 1_000_000_000
ITEM_CODES = list("ABCDEFGH")

# Non-Classic Gamemode Setup
GAME_MODES = ("classic", "apocalypse", "elimination")

# Help setup
HELP_FILE_INFO   = "readme_info.txt"
HELP_FILE_PLAYER = "readme_player.txt"
HELP_FILE_ADMIN  = "readme_admin.txt"
HELP_PAGE_LIMIT  = 4000  # stay under embed description limit


# Market View Allowed Channels
def _get_int_env(key: str) -> int | None:
    v = os.getenv(key)
    try:
        return int(v) if v else None
    except ValueError:
        return None

ALLOWED_MKVIEW_CHANNEL_IDS: set[int] = {
    x for x in (
        _get_int_env("MKVIEW_COUNCIL_ID"),
        _get_int_env("MKVIEW_GENERAL_ID"),
        _get_int_env("MKVIEW_STOCKS_ID"),
    ) if x
}

def _channel_or_parent_id(ch) -> int | None:
    return ch.id if getattr(ch, "parent", None) is None else ch.parent.id


# Base Format Layers
def _env_int(name: str) -> int | None:
    """Read a Discord snowflake from env; return None if missing/invalid."""
    v = os.getenv(name)
    try:
        return int(v) if v is not None and v.strip() != "" else None
    except (TypeError, ValueError):
        return None

ALLOWED_SIGNUP_CHANNEL_ID: int | None = _env_int("ALLOWED_SIGNUP_CHANNEL_ID")
ALLOWED_GAME_CATEGORY_ID: int | None = _env_int("ALLOWED_GAME_CATEGORY_ID")

def format_bank_history(history: list[dict]):
    history_text = ""
    for entry in history:
        if entry['change'] > 0:
            history_text += f"## +{entry['change']} hint points\n" \
                            f"Change: {entry['new_balance'] - entry['change']} hint points " \
                            f"-> {entry['new_balance']} hint points\n" \
                            f"User: <@{entry['user_id']}>\n" \
                            f"Time: <t:{entry['time']}:R>\n" \
                            f"Reason: {entry['reason']}\n"
        else:
            history_text += f"## {entry['change']} hint points\n" \
                            f"Change: {entry['new_balance'] - entry['change']} hint points " \
                            f"-> {entry['new_balance']} hint points\n" \
                            f"User: <@{entry['user_id']}>\n" \
                            f"Time: <t:{entry['time']}:R>\n" \
                            f"Reason: {entry['reason']}\n"
    return history_text

def format_history_embed(view: View):
    history_text = format_bank_history(view.history[view.index])
    history_embed = Embed(
        title=f"Hint Points History",
        description=history_text,
        colour=view.bank_owner.colour
    ).set_footer(text=f"{view.index + 1}/{len(view.history)}")
    user_avatar = None if view.bank_owner.avatar is None else view.bank_owner.avatar.url
    if view.bank_owner.discriminator == "0":
        history_embed = history_embed.set_author(name=view.bank_owner.name, icon_url=user_avatar)
    else:
        history_embed = history_embed.set_author(name=f"{view.bank_owner.name}#{view.bank_owner.discriminator}",
                                                 icon_url=user_avatar)
    return history_embed

def format_balance_embed(view: View):
    balance_embed = Embed(
        title=f"Hint Points",
        description=f"{view.balance} hint points",
        colour=view.bank_owner.colour
    )
    user_avatar = None if view.bank_owner.avatar is None else view.bank_owner.avatar.url
    if view.bank_owner.discriminator == "0":
        balance_embed = balance_embed.set_author(name=view.bank_owner.name, icon_url=user_avatar)
    else:
        balance_embed = balance_embed.set_author(name=f"{view.bank_owner.name}#{view.bank_owner.discriminator}",
                                                 icon_url=user_avatar)
    return balance_embed

def _normalize_hex(s: str) -> str | None:
    if not isinstance(s, str):
        return None
    s = s.strip()
    m = HEX_RE.fullmatch(s)
    if not m:
        return None
    core = s[1:] if s.startswith('#') else s
    return f"#{core.upper()}"

def _colour_from_hex(hex_str: str) -> Colour:
    # hex_str like "#RRGGBB"
    r = int(hex_str[1:3], 16)
    g = int(hex_str[3:5], 16)
    b = int(hex_str[5:7], 16)
    return Colour.from_rgb(r, g, b)

def _now_ts() -> int:
    return int(datetime.now().timestamp())

async def _get_market_config():
    return await db_client.market.config.find_one({"_id": "current"})  # {"items":{A:{name,price},...}}

def _portfolio_totals_with_mode(items_cfg: dict, portfolio: dict, use_next: bool) -> tuple[int, int]:
    """Return (unspent_cash, total_cash) where total = cash + sum(qty * price_mode)."""
    cash = int(portfolio.get("cash", 0))
    holdings = portfolio.get("holdings", {})
    total_val = 0
    for code in ITEM_CODES:
        q = int(holdings.get(code, 0))
        if q <= 0 or code not in items_cfg:
            continue
        base = int(items_cfg[code]["price"])
        price = int(items_cfg[code].get("next_price", base)) if use_next else base
        total_val += q * price
    return cash, cash + total_val

def signup_join_mention() -> str:
    try:
        return signup_join.get_mention(guild=None)
    except Exception:
        return "/signup join"
    
NO_BANK_MSG_SELF = (
    "You don’t have a Hint Point Inventory (you haven’t signed up yet).\n\n"
    f"Please run {signup_join_mention()} to sign up and create one (0 pt)."
)

def no_bank_msg_for(user_mention: str) -> str:
    return (
        f"{user_mention} does not have a Hint Point Inventory (they haven’t signed up yet).\n\n"
        f"If you are the host, please instruct them to run {signup_join_mention()}."
    )

def _item_label(code: str, items_cfg: dict | None) -> str:
    """Return 'A — Apple' if a name exists, otherwise 'A'."""
    info = (items_cfg or {}).get(code) or {}
    nm = info.get("name")
    return f"{code} — {nm}" if nm else code

def _signup_needed_msg_self() -> str:
    """Standard guidance when a user has no hint bank (not signed up)."""
    try:
        mention = signup_join.get_mention(guild=None)
    except Exception:
        mention = "/signup join"
    return (
        "You don’t have a Hint Point Inventory (you haven’t signed up yet).\n\n"
        f"Please run {mention} to sign up and create one (0 pt)."
    )

def _parse_orders(raw: str) -> list[tuple[str, int]]:
    if not raw or not raw.strip():
        raise ValueError("No orders found.")

    import re

    pairs: list[tuple[str, int]] = []
    # Split into chunks by , ; | or newline (names can contain spaces)
    chunks = re.split(r"[,\n;|]+", raw.strip())

    for chunk in chunks:
        s = chunk.strip()
        if not s:
            continue

        m = (
            re.match(r"^(?P<ident>.+?)\s*[:=]\s*(?P<qty>\d+)$", s) or   # A:10 / A=10
            re.match(r"^(?P<ident>.+?)\s+(?P<qty>\d+)$", s) or         # A 10 / Apple 5
            re.match(r"^(?P<qty>\d+)\s+(?P<ident>.+)$", s)             # 10 A / 3 Clothing
        )
        if not m:
            raise ValueError(f"Cannot parse pair: `{s}` (use 'Item 10', '10 Item', 'A:10', or 'A 10').")

        ident = m.group("ident").strip()
        qty = int(m.group("qty"))

        if qty < 1 or qty > MAX_ITEM_UNITS:
            raise ValueError(f"Quantity out of range for `{s}` (1–{MAX_ITEM_UNITS}).")

        # Normalize internal spacing in identifier (optional, helps matching)
        ident = re.sub(r"\s+", " ", ident)
        pairs.append((ident, qty))

    if not pairs:
        raise ValueError("No valid (item, quantity) pairs found.")

    return pairs

def fmt_pct2_signed(x: float) -> str:
    """Format with sign and 2 decimals, e.g., +12.34% / -0.50%."""
    return f"{x:+.2f}%"

# --- Global trading lock helpers ---
async def _trading_locked() -> bool:
    """Return True if the host has globally locked trading."""
    doc = await db_client.market.config.find_one({"_id": "current"}, {"trading_locked": 1})
    return bool(doc and doc.get("trading_locked"))

async def _set_trading_locked(flag: bool) -> None:
    """Flip the global trading lock on/off."""
    await db_client.market.config.update_one(
        {"_id": "current"},
        {"$set": {"trading_locked": bool(flag), "updated_at": int(datetime.now().timestamp())}},
        upsert=True,
    )

def _parse_admin_orders(raw: str) -> list[tuple[str, int]]:
    """
    Parse an orders string into [(identifier, qty)].
    Accepts separators: comma, semicolon, pipe, or newline.
    Each chunk is '<ident> <qty>', where ident is A–H or a case-insensitive item name.
    """
    import re
    if not raw or not raw.strip():
        raise ValueError("Empty orders.")
    chunks = re.split(r"[,\n;\|]+", raw)
    out: list[tuple[str, int]] = []
    pat = re.compile(r"^\s*(?P<ident>[A-Za-z ]{1,20})\s+(?P<qty>\d{1,9})\s*$")
    for ch in chunks:
        ch = ch.strip()
        if not ch:
            continue
        m = pat.match(ch)
        if not m:
            raise ValueError(f"Invalid order syntax: '{ch}'")
        ident = m.group("ident").strip()
        qty = int(m.group("qty"))
        out.append((ident, qty))
    if not out:
        raise ValueError("No valid orders found.")
    return out

def _resolve_item_code(items: dict, ident: str) -> str | None:
    if not ident:
        return None

    import re

    s = ident.strip()
    up = s.upper()
    if up in ITEM_CODES:
        return up

    # Normalize internal whitespace and case-fold for robust name matching
    norm = re.sub(r"\s+", " ", s).strip().casefold()

    for code in ITEM_CODES:
        info = items.get(code, {}) or {}
        nm = str(info.get("name", "")).strip()
        nm_norm = re.sub(r"\s+", " ", nm).casefold()
        if nm_norm == norm:
            return code

        # optional: alias list support
        aliases = info.get("aliases") or []
        for alias in aliases:
            alias_norm = re.sub(r"\s+", " ", str(alias)).strip().casefold()
            if alias_norm == norm:
                return code

    return None

# ---------- Private Category + public defer helpers ----------
def _in_game_category(inter: Interaction) -> bool:
    if ALLOWED_GAME_CATEGORY_ID is None:
        return True
    ch = inter.channel
    # direct category (normal text channel)
    cat = getattr(ch, "category", None)
    if getattr(cat, "id", None) == ALLOWED_GAME_CATEGORY_ID:
        return True
    # thread → check parent channel's category
    parent = getattr(ch, "parent", None)
    pcat = getattr(parent, "category", None)
    return getattr(pcat, "id", None) == ALLOWED_GAME_CATEGORY_ID

from functools import wraps
from typing import Iterable, Optional

def _channel_or_parent_id(ch) -> int | None:
    try:
        return ch.id if getattr(ch, "parent", None) is None else ch.parent.id
    except Exception:
        return getattr(ch, "id", None)

def guard(
    *,
    require_private: bool,
    public: bool,
    require_unlocked: bool = False,
    owner_only: bool = False,
    allow_channel_ids: Optional[Iterable[int]] = None,
):
    allow = set(allow_channel_ids or ())

    def deco(func):
        @wraps(func)
        async def wrapper(interaction: Interaction, *args, **kwargs):
            if owner_only and interaction.user.id != OWNER_ID:
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ Owner only.", ephemeral=False)
                else:
                    await interaction.followup.send("❌ Owner only.")
                return

            cid = _channel_or_parent_id(interaction.channel)
            whitelisted = (cid in allow)

            if require_private and not whitelisted and not _in_game_category(interaction):
                msg = f"❌ This command can only be used in channels under <#{ALLOWED_GAME_CATEGORY_ID}>."
                if not interaction.response.is_done():
                    await interaction.response.send_message(msg, ephemeral=False)
                else:
                    await interaction.followup.send(msg)
                return

            if public and not interaction.response.is_done():
                await interaction.response.defer(ephemeral=False)

            if require_unlocked and await _trading_locked():
                await interaction.followup.send("❌ Trading and hints are currently locked by the host.")
                return

            return await func(interaction, *args, **kwargs)
        return wrapper
    return deco

# ===================== Snapshots (for Revert) =====================

def _snap_col():
    return db_client.market.snapshots

async def _snapshot_state_for_liquidate(result_year: int) -> str:
    """Save a full restore point BEFORE we liquidate portfolios."""
    cfg = await db_client.market.config.find_one({"_id": "current"}) or {}
    portfolios = []
    async for pf in db_client.market.portfolios.find({}):
        portfolios.append({
            "_id": pf["_id"],
            "cash": int(pf.get("cash", 0)),
            "holdings": copy.deepcopy(pf.get("holdings", {})),
            # keep any flags you already use; harmless if absent
            "elim_locked": bool(pf.get("elim_locked", False)),
            "elim_locked_at_year": pf.get("elim_locked_at_year"),
            "global_locked": bool(pf.get("global_locked", False)),
        })
    doc = {
        "type": "liquidate",
        "result_year": int(result_year),
        "created_at": _now_ts(),
        "config": cfg,
        "portfolios": portfolios,
    }
    res = await _snap_col().insert_one(doc)
    return str(res.inserted_id)

async def _purge_snapshots(keep_last: int = 0) -> int:
    """
    Delete older liquidation snapshots.
    keep_last=1 -> keep only newest; keep_last=0 -> delete all.
    Returns number of deleted documents.
    """
    cur = _snap_col().find({"type": "liquidate"}).sort("created_at", -1).skip(max(keep_last, 0))
    ids = [doc["_id"] async for doc in cur]
    if not ids:
        return 0
    res = await _snap_col().delete_many({"_id": {"$in": ids}})
    return int(res.deleted_count)

# ===================== Pre-reveal snapshots (per year) =====================
async def _snapshot_pre_reveal(year: int) -> str:
    """
    Save all portfolios *before* revealing new prices for `year`.
    Use for targeted per-user rollback.
    """
    portfolios = []
    async for pf in db_client.market.portfolios.find({}):
        portfolios.append({
            "_id": pf["_id"],
            "cash": int(pf.get("cash", 0)),
            "holdings": copy.deepcopy(pf.get("holdings", {})),
            "updated_at": _now_ts(),
        })
    doc = {
        "type": "pre_reveal",
        "year": int(year),
        "created_at": _now_ts(),
        "portfolios": portfolios,
    }
    res = await _snap_col().insert_one(doc)
    return str(res.inserted_id)

# --- signup settings helpers
def _signup_cfg_col():
    return db_client.players.signup_settings  # single doc store

async def _get_signup_settings() -> dict:
    doc = await _signup_cfg_col().find_one({"_id": "current"})
    if not doc:
        doc = {"_id": "current", "started": False, "locked_at": None}
        await _signup_cfg_col().insert_one(doc)
    return doc

async def _set_game_started(started: bool) -> None:
    await _signup_cfg_col().update_one(
        {"_id": "current"},
        {"$set": {"started": bool(started), "locked_at": _now_ts() if started else None}},
        upsert=True
    )
# ===================== Game Mode Helpers =====================
async def _get_game_mode() -> str:
    """Return current game mode (default: classic)."""
    doc = await db_client.market.config.find_one({"_id": "current"}, {"game_mode": 1})
    mode = (doc or {}).get("game_mode", "classic")
    return mode if mode in GAME_MODES else "classic"

async def _is_eliminated_user(user_id: str) -> tuple[bool, int | None]:
    """Check if a portfolio is eliminated. Return (eliminated, elim_year)."""
    pf = await db_client.market.portfolios.find_one({"_id": str(user_id)}, {"eliminated": 1, "elim_year": 1})
    if not pf:
        return (False, None)
    if pf.get("eliminated"):
        return (True, int(pf.get("elim_year", 0)) or None)
    return (False, None)

# Store elimination snapshot to support fair final ranking later.

async def _mode_is(*targets: str) -> bool:
    """Return True if current game mode is one of targets."""
    mode = await _get_game_mode()
    return mode in targets

async def _safe_reply(interaction, content: str, public: bool = False):
    """Reply safely whether the interaction has been deferred or not."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=not public)
        else:
            await interaction.response.send_message(content, ephemeral=not public)
    except Exception:
        # As a last resort (rare), try followup
        await interaction.followup.send(content, ephemeral=not public)

def requires_mode(*modes: str, public: bool = False):
    """
    Decorator to gate a command to specific mode(s).
    Example: @requires_mode("elimination", public=True)
    """
    def deco(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Heuristic: find the Interaction argument
            interaction = kwargs.get("interaction", None)
            if interaction is None:
                # Usually the first positional arg is Interaction in our free functions
                for a in args:
                    # duck-typing to avoid importing nextcord types here
                    if hasattr(a, "response") and hasattr(a, "followup"):
                        interaction = a
                        break
            if interaction is None:
                # Fallback: just run
                return await func(*args, **kwargs)

            if not await _mode_is(*modes):
                want = " / ".join(modes)
                cur = await _get_game_mode()
                await _safe_reply(
                    interaction,
                    f"⛔ This command is available only in **{want}** mode(s). Current mode: **{cur}**.",
                    public=public,
                )
                return
            return await func(*args, **kwargs)
        return wrapper
    return deco

# -------- Hint: elimination-only self-use gate (keep once) --------

def disallow_self_hint_when_eliminated(public: bool = True):
    """
    For self-use hint commands (R/LVL1/LVL2/LVL3).
    - In elimination mode: if caller is eliminated, block.
    - In other modes: do nothing.
    """
    def deco(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            interaction = kwargs.get("interaction")
            if interaction is None:
                for a in args:
                    if hasattr(a, "response") and hasattr(a, "followup"):
                        interaction = a
                        break
            if interaction is None:
                return await func(*args, **kwargs)

            if await _mode_is("elimination"):
                is_elim, _ = await _is_eliminated_user(str(interaction.user.id))
                if is_elim:
                    msg = ("⛔ Eliminated players cannot use hint abilities for themselves. "
                           "You may still **transfer** hint points to support others.")
                    if interaction.response.is_done():
                        await interaction.followup.send(msg, ephemeral=not public)
                    else:
                        await interaction.response.send_message(msg, ephemeral=not public)
                    return
            return await func(*args, **kwargs)
        return wrapper
    return deco

async def _block_if_eliminated(interaction: Interaction, user_id: str) -> bool:
    """
    In elimination mode: if the given portfolio is eliminated, announce and block the action.
    Returns True if the action should be blocked.
    """
    if await _mode_is("elimination"):
        is_elim, elim_year = await _is_eliminated_user(user_id)
        if is_elim:
            await interaction.followup.send(
                "⛔ This portfolio has been **eliminated** and cannot trade (buy/sell)."
            )
            return True
    return False

# -------- Elimination round utils (add once if not present) --------
async def _current_result_year() -> int | None:
    """Read DB's last_result_year written after liquidate. None if not set."""
    cfg = await db_client.market.config.find_one({"_id": "current"}, {"last_result_year": 1})
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
    survivors = []
    async for pf in db_client.market.portfolios.find(
        {"$or": [{"eliminated": {"$exists": False}}, {"eliminated": False}]},
        {"cash": 1}
    ):
        survivors.append((pf["_id"], int(pf.get("cash", 0))))
    survivors.sort(key=lambda x: (x[1], x[0]))
    return survivors[:3]

# Expand _set_eliminated to record snapshot (if not already done)
async def _set_eliminated(user_id: str, year: int, *, cash: int | None = None, order: int | None = None) -> None:
    """Mark eliminated and store snapshot (cash/order) for fair ranking."""
    payload = {"eliminated": True, "elim_year": int(year), "updated_at": _now_ts()}
    if cash is not None:
        payload["elim_cash"] = int(cash)
    if order is not None:
        payload["elim_order"] = int(order)  # 1..3 within that round (1 = lowest cash)
    await db_client.market.portfolios.update_one({"_id": str(user_id)}, {"$set": payload}, upsert=False)

# ---- Ranking policy: 'survival' (default) or 'cash' ----
async def _get_elim_ranking_policy() -> str:
    doc = await db_client.market.config.find_one({"_id": "current"}, {"elim_ranking_policy": 1})
    pol = (doc or {}).get("elim_ranking_policy", "survival")
    return pol if pol in ("survival", "cash") else "survival"

async def _final_standings() -> list[tuple[str, int]]:
    policy = await _get_elim_ranking_policy()
    pcol = db_client.market.portfolios

    portfolios = []
    async for pf in pcol.find({}, {"cash":1,"eliminated":1,"elim_year":1,"elim_order":1,"elim_cash":1}):
        uid = pf["_id"]
        eliminated = bool(pf.get("eliminated"))
        cash_now = int(pf.get("cash", 0))
        elim_year = int(pf.get("elim_year", 0)) if eliminated else 0
        elim_order = int(pf.get("elim_order", 0)) if eliminated else 0
        elim_cash = int(pf.get("elim_cash", cash_now))
        portfolios.append((uid, eliminated, cash_now, elim_year, elim_order, elim_cash))

    if policy == "survival":
        # Survivors (not eliminated) ranked first by current cash desc.
        # Eliminated ranked after, by later elimination better; within same year, higher round-order better.
        portfolios.sort(
            key=lambda t: (
                1 if t[1] else 0,             # eliminated? survivors(0) first
                0 if not t[1] else -t[3],     # survivors: tie key 0; eliminated: -elim_year
                0 if not t[1] else -t[4],     # survivors: tie key 0; eliminated: -elim_order (3>2>1)
                -t[2] if not t[1] else 0,     # survivors: -cash_now
                t[0],                         # uid tie-breaker
            )
        )
    else:  # policy == "cash"
        # Rank purely by snapshot cash (survivors: current cash; eliminated: elim_cash)
        # Tie-breakers favor later elimination, then better order in round, then uid.
        portfolios.sort(
            key=lambda t: (
                - (t[2] if not t[1] else t[5]),   # -final_cash
                - (t[3] if t[1] else 9999),       # eliminated later → better (survivor tie uses big 9999)
                - (t[4] if t[1] else 3),          # within round, order 3>2>1
                t[0],
            )
        )

    # Return [(uid, rank_index starting at 1)]
    return [(uid, i+1) for i, (uid, *_rest) in enumerate(portfolios)]

async def _final_winner() -> tuple[str, int] | None:
    """
    Return (uid, cash) of the top-cash survivor (not eliminated).
    None if no survivors.
    """
    survivors = []
    async for pf in db_client.market.portfolios.find(
        {"$or": [{"eliminated": {"$exists": False}}, {"eliminated": False}]},
        {"cash": 1}
    ):
        survivors.append((pf["_id"], int(pf.get("cash", 0))))
    if not survivors:
        return None
    survivors.sort(key=lambda x: x[1], reverse=True)
    return survivors[0]

def _apoc_bucket(change_pct: int) -> str:
    """
    Return 'low' / 'medium' / 'high' bucket for the *next* fall amount (Apocalypse).
    You can adjust thresholds as you finalize the spec.
    """
    # Example thresholds based on your sheet:
    # low:   0 ~ -20
    # medium:-21 ~ -59
    # high:  <= -60
    if change_pct >= -20:
        return "low"
    if change_pct >= -59:
        return "medium"
    return "high"

def _apoc_low_fall_prob(years: list[dict]) -> int:
    """
    Start from 50% and add ODDS_APOC deltas based on past years (excluding the latest reveal).
    Clamp to 0..100.
    """
    base = 50
    years = sorted(years, key=lambda y: y.get("_id", 0))
    for y in years:
        for s in ITEM_CODES:
            if s in y:
                try:
                    base += int(ODDS_APOC.get(int(y[s]), 0))
                except Exception:
                    pass
    return max(0, min(100, base))

# ---------- year autocomplete (unchanged behavior) ----------
async def year_autocomplete(interaction: Interaction, year: str):
    years = []
    async for entry in db_client.stocks.changes.find({}, ["_id"]):
        years.append(entry["_id"])
    # Return as strings to be safe with autocomplete
    await interaction.response.send_autocomplete([str(x) for x in sorted(years)])
    return

def calculate_odds(years: list):
    """Compute per-stock odds (0..100) from year docs using the EXISTING ODDS mapping."""
    stocks = "ABCDEFGH"
    stock_odds = {s: 50 for s in stocks}
    years.sort(key=lambda y: y.get('_id', 0))
    for y in years:
        for s in stocks:
            if s not in y:
                continue
            change = int(y[s])
            stock_odds[s] += ODDS.get(change, 0)  # safe lookup
            stock_odds[s] = max(0, min(100, stock_odds[s]))
    return stock_odds

# ---------- Helpers for portfolio math ----------
def _portfolio_totals(items_cfg: dict, portfolio: dict) -> tuple[int, int]:
    """Return (unspent_cash, total_cash) where total = cash + sum(qty * price)."""
    cash = int(portfolio.get("cash", 0))
    holdings = portfolio.get("holdings", {})
    valuation = 0
    for code in ITEM_CODES:
        q = int(holdings.get(code, 0))
        if q <= 0:
            continue
        price = int(items_cfg[code]["price"])
        valuation += q * price
    return cash, cash + valuation


# Base Format Layers





# Help Format Layers
def _read_text_file(path: str) -> str | None:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return None
    return p.read_text(encoding="utf-8", errors="replace")

def _chunk_text(s: str, limit: int = HELP_PAGE_LIMIT) -> list[str]:
    if not s:
        return [""]
    chunks, cur, cur_len = [], [], 0
    for line in s.splitlines(keepends=True):
        if cur_len + len(line) > limit:
            if cur:
                chunks.append("".join(cur))
                cur, cur_len = [], 0
            while len(line) > limit:   # super-long single line
                chunks.append(line[:limit])
                line = line[limit:]
        cur.append(line)
        cur_len += len(line)
    if cur:
        chunks.append("".join(cur))
    return chunks or [""]

def _help_title(kind: str) -> str:
    return {"info": "Help — Info", "player": "Help — Player", "admin": "Help — Admin"}[kind]

def _load_help_pages(kind: str) -> tuple[list[str] | None, str]:
    if kind == "info":
        txt = _read_text_file(HELP_FILE_INFO)
    elif kind == "player":
        txt = _read_text_file(HELP_FILE_PLAYER)
    else:
        txt = _read_text_file(HELP_FILE_ADMIN)
    if txt is None:
        return None, _help_title(kind)
    return _chunk_text(txt), _help_title(kind)

def _md_escape(s: str) -> str:
    """Escape Discord markdown so underscores etc. show literally."""
    if s is None:
        return ""
    return (str(s)
            .replace("\\", "\\\\")
            .replace("_", "\\_")
            .replace("*", "\\*")
            .replace("`", "\\`")
            .replace("~", "\\~")
            .replace("|", "\\|")
            .replace(">", "\\>"))
# Help Format Layers





# Base Class Layers
class HistoryLeftButton(Button):
    def __init__(self):
        super().__init__(style=ButtonStyle.blurple, label="Previous", row=1)

    async def callback(self, interaction: Interaction):
        if self.view.index != 0:
            self.view.index -= 1
        else:
            self.view.index = len(self.view.history) - 1
        embed = format_history_embed(self.view)
        await interaction.response.edit_message(embed=embed)

class HistoryRightButton(Button):
    def __init__(self):
        super().__init__(style=ButtonStyle.blurple, label="Next", row=1)

    async def callback(self, interaction: Interaction):
        if len(self.view.history) - 1 == self.view.index:
            self.view.index = 0
        else:
            self.view.index = self.view.index + 1
        embed = format_history_embed(self.view)
        await interaction.response.edit_message(embed=embed)

class HistoryToBalanceButton(Button):
    def __init__(self):
        super().__init__(style=ButtonStyle.grey, label="View Balance", row=2, emoji="💵")

    async def callback(self, interaction: Interaction):
        balance_view = BankBalanceViewer(self.view.index, self.view.balance, self.view.history, self.view.bank_owner)
        balance_embed = format_balance_embed(self.view)
        await interaction.response.edit_message(embed=balance_embed, view=balance_view)

class BalanceToHistoryButton(Button):
    def __init__(self):
        super().__init__(style=ButtonStyle.blurple, label="View History", row=1, emoji="📜")

    async def callback(self, interaction: Interaction):
        history_view = BankHistoryViewer(self.view.index, self.view.balance, self.view.history, self.view.bank_owner)
        history_embed = format_history_embed(self.view)
        await interaction.response.edit_message(embed=history_embed, view=history_view)

class BankHistoryViewer(View):
    def __init__(self, index, balance, history, bank_owner: Member):
        super().__init__()
        self.index = index
        self.add_item(HistoryLeftButton())
        self.add_item(HistoryRightButton())
        self.add_item(HistoryToBalanceButton())
        self.message = None
        self.timeout = 3600
        self.balance = balance
        self.history = history
        self.bank_owner = bank_owner

class BankBalanceViewer(View):
    def __init__(self, index, balance, history, bank_owner: Member):
        super().__init__()
        self.index = index
        self.add_item(BalanceToHistoryButton())
        self.message = None
        self.timeout = 3600
        self.balance = balance
        self.history = history
        self.bank_owner = bank_owner

def paginate_list(array: list, entries_per_page: int = None):
    if entries_per_page is None:
        entries_per_page = 10
    ten_array = []
    newer_array = []
    for entry in array:
        ten_array.append(entry)
        if len(ten_array) == entries_per_page:
            newer_array.append(ten_array)
            ten_array = []
    if ten_array:
        newer_array.append(ten_array)
    return newer_array

# Base Class Layers





# Help Class Layers
class HelpView(View):
    """Dropdown-only help view (Info/Player/Admin). Only the invoker may interact."""
    def __init__(self, invoker_id: int, kind: str, owner_id: int):
        super().__init__(timeout=600)
        self.invoker_id = invoker_id
        self.owner_id = owner_id
        self.kind = kind                      # 'info' | 'player' | 'admin'
        self.pages, self.title = _load_help_pages(kind)
        self.page = 0                         # always show page 0
        self.message = None

        # Section selector (Admin is gated by owner)
        self.selector = Select(
            placeholder="Select help section…",
            options=[
                SelectOption(label="Info",   value="info",   description="Overview & rules"),
                SelectOption(label="Player", value="player", description="Player commands"),
                SelectOption(label="Admin",  value="admin",  description="Owner-only commands"),
            ],
            min_values=1, max_values=1
        )
        self.selector.callback = self._on_select
        self.add_item(self.selector)

    async def interaction_check(self, inter) -> bool:
        if inter.user.id != self.invoker_id:
            await inter.response.send_message("Only the original requester can use this.", ephemeral=True)
            return False
        return True

    async def _on_select(self, inter):
        val = self.selector.values[0]
        if val == "admin" and inter.user.id != self.owner_id:
            await inter.response.send_message("Admin help is available to the owner only.", ephemeral=True)
            self._sync_select_defaults()
            try:
                await inter.edit_original_message(view=self)
            except Exception:
                pass
            return

        self.kind = val
        self.pages, self.title = _load_help_pages(self.kind)
        self.page = 0
        if self.pages is None:
            await inter.response.edit_message(
                embed=Embed(title=self.title, description=f"❌ Missing file for **{self.kind}** help.", colour=BOT_COLOUR),
                view=self
            )
            return
        await inter.response.edit_message(embed=self._cur_embed(), view=self)

    def _sync_select_defaults(self):
        for opt in self.selector.options:
            opt.default = (opt.value == self.kind)

    def _cur_embed(self) -> Embed:
        total = len(self.pages or [])
        desc = (self.pages[0] if self.pages else "—")  # always first page
        emb = Embed(title=self.title, description=desc, colour=BOT_COLOUR)
        if total > 1:
            emb.set_footer(text=f"Page 1/{total}")
        self._sync_select_defaults()
        return emb
# Help Class Layers





# ============= "signup" Group of Commands =============
@bot.slash_command(
    name="signup",
    description="Player signup & roster tools",
    force_global=True
)
async def signup_cmd(interaction: Interaction):
    pass

# ============= Subcommand "join" of "signup"
@signup_cmd.subcommand(
    name="join",
    description="Sign up and create your hint point inventory (0 pt).",
)
async def signup_join(
        interaction: Interaction,
        color_name: str = SlashOption(
            description="Your color name (letters/spaces only, max 20)",
            required=True, max_length=20  # enforce 20 at UI layer
        ),
        color_hex: str = SlashOption(
            description="Your color HEX (e.g., #FF0000 or FF0000)",
            required=True
        ),
):
    # 1) Channel restriction (allow the channel or its thread)
    ch = interaction.channel
    if ALLOWED_SIGNUP_CHANNEL_ID is not None:
        in_allowed = (
            interaction.channel_id == ALLOWED_SIGNUP_CHANNEL_ID
            or getattr(ch, "parent_id", None) == ALLOWED_SIGNUP_CHANNEL_ID
        )
        if not in_allowed:
            await interaction.response.send_message(
                f"❌ You can only use this command in <#{ALLOWED_SIGNUP_CHANNEL_ID}>.",
                ephemeral=True
            )
            return
    # We’re in an allowed place (or no env set) → defer publicly
    if not interaction.response.is_done():
        await interaction.response.defer()  # public

    settings = await _get_signup_settings()
    if bool(settings.get("started")):
        await interaction.followup.send("🔒 Signups are locked — the game has already started.")
        return

    # 2) Collections & capacity check
    players_db = db_client.players
    signups_col = players_db.signups
    hp_db = db_client.hint_points
    balance_col = hp_db.balance
    portfolios = db_client.market.portfolios

    current_count = await signups_col.count_documents({})
    if current_count >= MAX_PLAYERS:
        await interaction.followup.send(
            f"❌ Capacity full: {MAX_PLAYERS}/{MAX_PLAYERS}. Signups are closed."
        )
        return

    user_id = str(interaction.user.id)

    # 3) If already in signups, reject (true duplicate)
    existing_signup = await signups_col.find_one({"_id": user_id})
    if existing_signup is not None:
        await interaction.followup.send("❌ You have already signed up. You can only sign up once.")
        return

    # 4) Cleanup orphaned records BEFORE any “already have a bank” rejection.
    #    If signups is missing but old hint bank / portfolio still exists, delete them.
    existing_bank = await balance_col.find_one({"_id": user_id})
    existing_port = await portfolios.find_one({"_id": user_id})
    if existing_bank:
        await balance_col.delete_one({"_id": user_id})
    if existing_port:
        await portfolios.delete_one({"_id": user_id})

    # 5) Validate inputs
    clean_name = color_name.strip()
    if not COLOR_NAME_RE.fullmatch(clean_name):
        await interaction.followup.send(
            "❌ Invalid color name. Use only English letters and spaces, up to 20 characters."
        )
        return

    norm_hex = _normalize_hex(color_hex)
    if norm_hex is None:
        await interaction.followup.send(
            "❌ Invalid HEX code. Provide a 6-digit HEX like `#RRGGBB`."
        )
        return

    # --- Duplicate checks (case-insensitive for name, normalized for HEX) ---
    hex_norm = norm_hex.upper()  # '#RRGGBB' 형태라고 가정 (_normalize_hex 사용했다면 일관)
    
    dup_name = await signups_col.find_one(
        {"color_name": {"$regex": f"^{re.escape(clean_name)}$", "$options": "i"}}
    )
    if dup_name:
        await interaction.followup.send(
            "❌ This color name is already taken. Choose a different name."
        )
        return
    
    dup_hex = await signups_col.find_one({"color_hex": {"$regex": f"^{re.escape(hex_norm)}$", "$options": "i"}})
    if dup_hex:
        await interaction.followup.send(
            "❌ This HEX code is already used by another player. Choose a different HEX."
        )
        return

    # 6) Create signup record (user_id is stored but not displayed)
    await signups_col.insert_one({
        "_id": user_id,
        "user_id": user_id,
        "user_name": interaction.user.name,
        "color_name": clean_name,
        "color_hex": norm_hex,
        "signup_time": _now_ts(),
    })

    # 7) Create 0pt hint bank with history
    await balance_col.insert_one({
        "_id": user_id,
        "balance": 0,
        "history": [{
            "time": _now_ts(),
            "change": 0,
            "new_balance": 0,
            "user_id": str(bot.user.id),
            "reason": "Signup - inventory opened (0 pt)"
        }],
    })

    mode = await _get_game_mode()
    start_cash = APOC_START_CASH if mode == "apocalypse" else STARTING_CASH
    
    # 8) Create portfolio with starting cash and empty holdings
    await portfolios.insert_one({
        "_id": user_id,
        "user_id": user_id,
        "cash": int(start_cash),                     # ← was STARTING_CASH
        "holdings": {code: 0 for code in ITEM_CODES},
        "updated_at": _now_ts(),
        "history": []
    })

    # 9) Acknowledge
    remaining = MAX_PLAYERS - (current_count + 1)
    embed = Embed(
        title="✅ Signup Complete",
        description=(
            f"**Player**: {interaction.user.mention}\n"
            f"**Color**: {clean_name} `{norm_hex}`\n"
            f"**Starting Cash**: {int(start_cash)}\n"   # ← add this line
            f"**Slots left until closing**: **{remaining}** / {MAX_PLAYERS}"
        ),
        colour=_colour_from_hex(norm_hex)
    )
    await interaction.followup.send(embed=embed)

# ============= Subcommand "view" of "signup"
@signup_cmd.subcommand(
    name="view",
    description="View the roster: [Username] - [Color Name] - [HEX]",
)
async def signup_view(interaction: Interaction):
    # Permission for OWNER_ID Only.
    if interaction.user.id != OWNER_ID:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("❌ Only the owner can run this command.")
        return

    await interaction.response.defer()  # public

    players_db = db_client.players
    signups_col = players_db.signups

    lines = []
    cursor = signups_col.find(
        {}, projection={"user_name": 1, "color_name": 1, "color_hex": 1, "signup_time": 1}
    ).sort("signup_time", 1)
    
    async for d in cursor:
        uname = _md_escape(d.get("user_name", "(unknown)"))
        cname = _md_escape(d.get("color_name", "?"))
        chex  = _md_escape(d.get("color_hex", "?"))
        lines.append(f"{uname} — {cname} — `{chex}`")

    roster = "\n".join(lines) if lines else "_No signups yet_"
    embed = Embed(
        title=f"Signup Roster ({len(lines)}/{MAX_PLAYERS})",
        description=roster,
        colour=BOT_COLOUR
    )
    await interaction.followup.send(embed=embed)

# ========= signup reset (interactive confirm) =========
@signup_cmd.subcommand(
    name="reset",
    description="Owner-only: Set NEW items first, then wipe signups/hint banks/portfolios and apply."
)
async def signup_reset(
    interaction: Interaction,
    names: str = SlashOption(
        description="8 names separated by | (letters/spaces only, 1~20 chars each)",
        required=True
    ),
    prices: str = SlashOption(
        description="8 integer prices separated by |",
        required=True
    ),
    confirm: str = SlashOption(
        description="Type CONFIRM to prepare the preview. Actual wipe happens after pressing the button.",
        required=True
    ),
    mode: str = SlashOption(                         # ← NEW: game mode selector
        description="Game mode (classic/apocalypse/elimination)",
        required=False,
        choices=["classic", "apocalypse", "elimination"]
    ),
):
    # Permission
    if interaction.user.id != OWNER_ID:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("❌ Only the owner can run this command.")
        return

    await interaction.response.defer(ephemeral=True)

    # Primary Safety
    if confirm != "CONFIRM":
        await interaction.followup.send("❌ Type `CONFIRM` to prepare reset preview.")
        return

    # Input Parsing
    name_list = [s.strip() for s in names.split("|")]
    price_list = [s.strip() for s in prices.split("|")]
    if len(name_list) != 8 or len(price_list) != 8:
        await interaction.followup.send("❌ Provide exactly 8 names and 8 prices, separated by `|`.")
        return

    NAME_RE = re.compile(r"^[A-Za-z ]{1,20}$")
    for i, nm in enumerate(name_list):
        if not NAME_RE.fullmatch(nm):
            await interaction.followup.send(f"❌ Invalid name for {ITEM_CODES[i]}: `{nm}` (letters/spaces, 1~20).")
            return

    try:
        price_vals = [int(x) for x in price_list]
    except ValueError:
        await interaction.followup.send("❌ Prices must be integers.")
        return
    if any(p <= 0 for p in price_vals):
        await interaction.followup.send("❌ All prices must be positive integers.")
        return

    # Resolve target mode (default classic)
    selected_mode = (mode or "classic").strip().lower()
    if selected_mode not in ("classic", "apocalypse", "elimination"):
        await interaction.followup.send("❌ Invalid game mode. Choose one of: classic, apocalypse, elimination.")
        return

    # Build "base" items from input
    base_items = {code: {"name": name_list[i], "price": price_vals[i]} for i, code in enumerate(ITEM_CODES)}

    # Prepare "applied" items preview (Apocalypse multiplies by 100)
    applied_items = {c: dict(base_items[c]) for c in ITEM_CODES}
    if selected_mode == "apocalypse":
        for c in ITEM_CODES:
            applied_items[c]["price"] = int(applied_items[c]["price"]) * 100  # 100× scale at apply-time

    # String previews
    base_preview   = "\n".join([f"{c}: **{base_items[c]['name']}** — {base_items[c]['price']}" for c in ITEM_CODES])
    final_preview  = "\n".join([f"{c}: **{applied_items[c]['name']}** — {applied_items[c]['price']}" for c in ITEM_CODES])
    mode_note = {
        "classic":      "Classic mode. Prices are applied as-is.",
        "apocalypse":   "Apocalypse mode. **Starting cash = 1,000,000,000**. **All base prices are scaled ×100**. R-hint disabled; L1~L3 use apocalypse rules.",
        "elimination":  "Elimination mode. Uses standard pricing; elimination cuts run on DB 5~10."
    }[selected_mode]

    # ---------- Confirmation View ----------
    class ResetConfirmView(View):
        def __init__(self, owner_id: int, base_items: dict, applied_items: dict, mode: str):
            super().__init__(timeout=600)
            self.owner_id = owner_id
            self.base_items = base_items          # raw user input
            self.applied_items = applied_items    # what will actually be written
            self.mode = mode                      # "classic" | "apocalypse" | "elimination"
            self.message = None

        async def interaction_check(self, btn_inter: Interaction) -> bool:
            if btn_inter.user.id != self.owner_id:
                await btn_inter.response.send_message("❌ Only the owner can confirm/cancel this.", ephemeral=True)
                return False
            return True

        async def disable_all(self):
            for c in self.children:
                c.disabled = True
            try:
                await self.message.edit(view=self)
            except:
                pass

    class ApplyButton(Button):
        def __init__(self):
            super().__init__(style=ButtonStyle.danger, label="Apply & Wipe", emoji="🗑️")

        async def callback(self, btn_inter: Interaction):
            # ACK immediately to avoid timeout
            await btn_inter.response.defer()  # preview was ephemeral, followups stay under original

            # 1) Wipe collections
            try:
                sres = await db_client.players.signups.delete_many({})
                bres = await db_client.hint_points.balance.delete_many({})
                pres = await db_client.market.portfolios.delete_many({})
                cres = await db_client.stocks.changes.delete_many({})
                try:
                    await db_client.stocks.prices.delete_many({})
                    await db_client.market.snapshots.delete_many({})
                except Exception:
                    pass
            except Exception as e:
                await btn_inter.edit_original_message(content=f"❌ Error while wiping collections: {e}", view=None)
                return

            # 2) Write new market config (with mode + applied prices)
            try:
                cfg_col = db_client.market.config

                # Payload that goes to DB
                clean_items = {code: {"name": self.view.applied_items[code]["name"],
                                      "price": int(self.view.applied_items[code]["price"])}
                               for code in ITEM_CODES}

                payload = {
                    "items": clean_items,
                    "game_mode": self.view.mode,                 # persist game mode
                    "updated_at": int(datetime.now().timestamp()),
                    # reset per-season markers
                    "last_result_year": 0,
                    "final_announced": False,
                    "final_winner": None
                }

                # Extra flags for apocalypse (helpful for joins & guards)
                if self.view.mode == "apocalypse":
                    payload["apoc_start_cash"] = 1_000_000_000   # starting cash hint (join logic should read mode anyway)

                await cfg_col.update_one(
                    {"_id": "current"},
                    {"$set": payload,
                     "$unset": {"use_next_for_total": "", "next_year": ""}},
                    upsert=True
                )
                await _set_game_started(False)

                # 3) Edit the original message
                lines = [f"{c}: **{clean_items[c]['name']}** — {clean_items[c]['price']}" for c in ITEM_CODES]
                await btn_inter.edit_original_message(
                    embed=Embed(
                        title="✅ Reset Applied",
                        description=(
                            f"**Mode:** `{self.view.mode}`\n"
                            f"{mode_note}\n\n"
                            "**Wiped collections**\n"
                            f"- Deleted signups: {sres.deleted_count}\n"
                            f"- Deleted hint banks: {bres.deleted_count}\n"
                            f"- Deleted portfolios: {pres.deleted_count}\n"
                            f"- Deleted stock changes: {cres.deleted_count}\n\n"
                            "**New Items (A~H)**\n" + "\n".join(lines)
                        ),
                        colour=BOT_COLOUR
                    ),
                    view=None
                )
            except Exception as e:
                await btn_inter.edit_original_message(content=f"❌ Error while writing new market config: {e}", view=None)

    class CancelButton(Button):
        def __init__(self):
            super().__init__(style=ButtonStyle.secondary, label="Cancel", emoji="🚫")

        async def callback(self, btn_inter: Interaction):
            await btn_inter.response.edit_message(
                embed=Embed(
                    title="❎ Reset Cancelled",
                    description="No data was deleted or changed.",
                    colour=BOT_COLOUR
                ),
                view=None
            )

    view = ResetConfirmView(OWNER_ID, base_items, applied_items, selected_mode)
    view.add_item(ApplyButton())
    view.add_item(CancelButton())

    embed = Embed(
        title="⚠️ Reset Preview",
        description=(
            f"**Mode:** `{selected_mode}`\n"
            f"{mode_note}\n\n"
            "If you press **Apply & Wipe**, the bot will:\n"
            "1) Delete **signups**, **hint point inventories**, **portfolios**, and **stock changes**.\n"
            "2) Replace **market items** with the following A~H config.\n\n"
            "**Input (Base) Prices**\n" + base_preview +
            "\n\n**Applied Prices**\n" + final_preview
        ),
        colour=BOT_COLOUR
    )

    msg = await interaction.followup.send(embed=embed, view=view)
    view.message = msg

@signup_cmd.subcommand(
    name="config",
    description="Open the signup panel (self-config + admin tools)."
)
async def signup_config(interaction: Interaction):
    # Single defer (public)
    if not interaction.response.is_done():
        await interaction.response.defer()

    players_db = db_client.players
    signups_col = players_db.signups

    user_id = str(interaction.user.id)
    signup_doc = await signups_col.find_one({"_id": user_id})
    settings = await _get_signup_settings()

    # Current game mode & min player rule
    cur_mode = await _get_game_mode()  # "classic" | "apocalypse" | "elimination"
    MIN_START_PLAYERS = 16 if cur_mode in ("classic", "apocalypse") else MAX_PLAYERS

    cur_count = await signups_col.count_documents({})
    slots_left = max(0, MAX_PLAYERS - cur_count)
    locked = bool(settings.get("started"))

    if signup_doc:
        summary = (f"**You are signed up.**\n"
                   f"Name: **{signup_doc['color_name']}**  •  HEX: `{signup_doc['color_hex']}`")
    else:
        summary = "You have **not** signed up yet. Use **/signup join** in the signup channel."

    lock_line = "🔒 **Game Started** — signups & edits are locked." if locked else "🟢 Signups are **open**."
    slots_line = f"**Slots:** {cur_count} / {MAX_PLAYERS}" + (f"  •  ({slots_left} left)" if not locked else "")
    mode_line = f"**Mode:** `{cur_mode}`  •  **Min to start:** {MIN_START_PLAYERS}"

    embed = Embed(
        title="Signup — Configuration",
        description=f"{lock_line}\n{slots_line}\n{mode_line}\n\n{summary}",
        colour=BOT_COLOUR
    )

    class SignupPanel(View):
        """Self edit + owner start/lock toggle (+ Close)."""
        def __init__(self, owner_id: int, started: bool, user_has_signup: bool,
                     cur_count: int, slots_left: int, min_required: int, mode: str):
            super().__init__(timeout=600)
            self.owner_id = owner_id
            self.started = started
            self.user_has_signup = user_has_signup
            self.cur_count = cur_count
            self.slots_left = slots_left
            self.min_required = min_required
            self.mode = mode

            # --- Help (anyone) ---
            join_btn = Button(style=ButtonStyle.primary, label="How do I sign up?")
            join_btn.callback = self.on_join_help
            join_btn.disabled = self.started
            self.add_item(join_btn)

            # --- Self edit (only if signed up & not started) ---
            edit_btn = Button(style=ButtonStyle.secondary, label="Edit My Color/HEX", emoji="🎨")
            edit_btn.callback = self.on_edit
            edit_btn.disabled = not (self.user_has_signup and not self.started)
            self.add_item(edit_btn)

            # --- Owner: Start/Unlock (min rule applied) ---
            can_start = (not self.started) and (self.cur_count >= self.min_required)
            gs_label = "Start Game (Lock Signups)" if not self.started else "Unlock Signups"
            gs_emoji = "🚀" if not self.started else "🔓"
            start_btn = Button(style=ButtonStyle.success if not self.started else ButtonStyle.secondary,
                               label=gs_label, emoji=gs_emoji)
            start_btn.callback = self.on_toggle_start
            start_btn.disabled = (interaction.user.id != OWNER_ID) or (not self.started and not can_start)
            self.add_item(start_btn)

            # --- Close (just remove components) ---
            close_btn = Button(style=ButtonStyle.secondary, label="Close", emoji="❌")
            close_btn.callback = self.on_close
            self.add_item(close_btn)

        async def on_join_help(self, btn_inter: Interaction):
            await btn_inter.response.send_message(
                "Use **/signup join** in the designated signup channel to register.\n"
                "You’ll choose your color name and HEX; capacity is limited.",
                ephemeral=True
            )

        async def on_edit(self, btn_inter: Interaction):
            if self.started or not self.user_has_signup:
                await btn_inter.response.send_message("❌ Editing is locked.", ephemeral=True)
                return

            class EditModal(Modal):
                def __init__(self):
                    super().__init__("Edit Color / HEX")
                    self.color_name = TextInput(
                        label="Color Name (letters & spaces, 1~20)",
                        required=True,
                        max_length=20,
                        default_value=signup_doc["color_name"] if signup_doc else "",
                    )
                    self.color_hex = TextInput(
                        label="HEX (e.g., #FF00AA or FF00AA)",
                        required=True,
                        default_value=signup_doc["color_hex"] if signup_doc else "",
                    )
                    self.add_item(self.color_name)
                    self.add_item(self.color_hex)

                async def callback(self, modal_inter: Interaction):
                    name = self.color_name.value.strip()
                    hexv = self.color_hex.value.strip()

                    if not COLOR_NAME_RE.fullmatch(name):
                        await modal_inter.response.send_message(
                            "❌ Invalid color name. Use only English letters and spaces, up to 20 characters.",
                            ephemeral=True
                        )
                        return

                    norm = _normalize_hex(hexv)
                    if norm is None:
                        await modal_inter.response.send_message(
                            "❌ Invalid HEX code. Provide a 6-digit HEX like `#RRGGBB`.",
                            ephemeral=True
                        )
                        return

                    await signups_col.update_one(
                        {"_id": user_id},
                        {"$set": {"color_name": name, "color_hex": norm,
                                  "signup_time": signup_doc.get("signup_time") if signup_doc else _now_ts()}}
                    )
                    await modal_inter.response.send_message(f"✅ Updated: **{name}** `{norm}`", ephemeral=True)

            await btn_inter.response.send_modal(EditModal())

        async def on_toggle_start(self, btn_inter: Interaction):
            if btn_inter.user.id != self.owner_id:
                await btn_inter.response.send_message("❌ Owner only.", ephemeral=True)
                return

            live_count = await signups_col.count_documents({})
            if not self.started and live_count < self.min_required:
                await btn_inter.response.send_message(
                    f"⏳ Need at least **{self.min_required}** players to start "
                    f"(current: {live_count}/{self.min_required}).",
                    ephemeral=True
                )
                return

            new_state = not self.started
            await _set_game_started(new_state)
            self.started = new_state

            live_left = max(0, MAX_PLAYERS - live_count)
            lock_line = "🔒 **Game Started** — signups & edits are locked." if new_state else "🟢 Signups are **open**."
            slots_line = f"**Slots:** {live_count} / {MAX_PLAYERS}" + (f"  •  ({live_left} left)" if not new_state else "")
            mode_line = f"**Mode:** `{self.mode}`  •  **Min to start:** {self.min_required}"
            new_view = SignupPanel(self.owner_id, self.started, self.user_has_signup, live_count, live_left,
                                   self.min_required, self.mode)
            await btn_inter.response.edit_message(
                embed=Embed(
                    title="Signup — Configuration",
                    description=f"{lock_line}\n{slots_line}\n{mode_line}\n\n{summary}",
                    colour=BOT_COLOUR
                ),
                view=new_view
            )

        async def on_close(self, btn_inter: Interaction):
            for c in self.children:
                c.disabled = True
            await btn_inter.response.edit_message(view=None)

    await interaction.followup.send(
        embed=embed,
        view=SignupPanel(OWNER_ID, locked, signup_doc is not None, cur_count, slots_left, MIN_START_PLAYERS, cur_mode)
    )

# ============= Subcommand "remove" of "signup" (OWNER only) =============
@signup_cmd.subcommand(
    name="remove",
    description="OWNER: Remove a signed-up player and purge their data."
)
@guard(require_private=False, public=True, owner_only=True)
async def signup_remove(
    interaction: Interaction,
    user: Member = SlashOption(description="Player to remove", required=True),
    confirm: str = SlashOption(description='Type "CONFIRM" to proceed.', required=True)
):
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    if confirm != "CONFIRM":
        await interaction.followup.send("❌ Type `CONFIRM` to proceed.")
        return

    uid = str(user.id)
    signups_col = db_client.players.signups
    deleted = 0
    deleted += (await signups_col.delete_one({"_id": uid})).deleted_count
    deleted += (await db_client.hint_points.balance.delete_one({"_id": uid})).deleted_count
    deleted += (await db_client.market.portfolios.delete_one({"_id": uid})).deleted_count

    await interaction.followup.send(
        f"✅ Removed {user.mention} (deleted docs total: **{deleted}**)."
    )
#===================================================





#===================================================
# ============= General "hint_points" Group of Commands
@bot.slash_command(
    name="hint_points",
    description="Manage your hint points",
    force_global=True)
async def hint_points_cmd(interaction: Interaction):
    pass
#===================================================
# ============= Subcommand "add" of "hint_points"
@hint_points_cmd.subcommand(
    description="Add hint_points.",
)
async def add(
        interaction: Interaction,
        user: Member = SlashOption(
            description="The person you want to add hint points to.",
            required=True,
        ),
        hint_points: int = SlashOption(
            description="The number of hint points you want to add.",
            required=True,
            min_value=1,
        ),
        reason: str = SlashOption(
            description="Why you added these hint_points.",
            required=True,
        ),
):
    await interaction.response.defer()
    if not interaction.user.id == OWNER_ID:
        await interaction.followup.send("You are not Lunarisk. You cannot set up hint points. Go away.")
        return
    db = db_client.hint_points
    collection = db.balance
    bank = await collection.find_one({"_id": str(user.id)})
    if bank is None:
        await interaction.followup.send(no_bank_msg_for(user.mention))
        return
    else:
        balance = bank["balance"]
    history_entry = {
        "time": int(datetime.now().timestamp()),
        "change": hint_points,
        "new_balance": balance + hint_points,
        "user_id": str(interaction.user.id),
        "reason": reason
    }
    bank["history"].append(history_entry)
    updates = {
        "balance": balance + hint_points,
        "history": bank["history"],
    }
    await collection.update_one({"_id": str(user.id)}, {"$set": updates})
    existing_bank = await collection.find_one({"_id": str(user.id)})
    existing_bank["history"].sort(key=lambda x: x["time"], reverse=True)
    history = paginate_list(existing_bank["history"])
    balance_view = BankBalanceViewer(0, existing_bank["balance"], history, user)
    balance_embed = format_balance_embed(balance_view)
    msg = await interaction.followup.send(
        content=f"Successfully added {hint_points} hint points to {user.mention}",
        embed=balance_embed,
        view=balance_view,
        allowed_mentions=AllowedMentions(everyone=False, roles=False, users=[user]),
    )
#===================================================
# ============= Subcommand "remove" of "hint_points"
@hint_points_cmd.subcommand(
    description="Remove hint points.",
)
async def remove(
        interaction: Interaction,
        user: Member = SlashOption(
            description="The person you want to remove hint points from.",
            required=True,
        ),
        hint_points: int = SlashOption(
            description="The number of hint points you want to remove.",
            required=True,
            min_value=1,
        ),
        reason: str = SlashOption(
            description="Why you removed these hint points.",
            required=True,
        ),
):
    await interaction.response.defer()
    if not interaction.user.id == OWNER_ID:
        await interaction.followup.send("You are not Lunarisk. You cannot set up hint points. Go away.")
        return
    db = db_client.hint_points
    collection = db.balance
    bank = await collection.find_one({"_id": str(user.id)})
    if bank is None:
        await interaction.followup.send(no_bank_msg_for(user.mention))
        return
    balance = bank["balance"]
    if balance - hint_points < 0:
        await interaction.followup.send(f"That would put {user.mention} into debt. They only have {balance} hint points.")
        return
    history_entry = {
        "time": int(datetime.now().timestamp()),
        "change": -hint_points,
        "new_balance": balance - hint_points,
        "user_id": str(interaction.user.id),
        "reason": reason
    }
    bank["history"].append(history_entry)
    updates = {
        "balance": balance - hint_points,
        "history": bank["history"],
    }
    await collection.update_one({"_id": str(user.id)}, {"$set": updates})
    existing_bank = await collection.find_one({"_id": str(user.id)})
    existing_bank["history"].sort(key=lambda x: x["time"], reverse=True)
    history = paginate_list(existing_bank["history"])
    balance_view = BankBalanceViewer(0, existing_bank["balance"], history, user)
    balance_embed = format_balance_embed(balance_view)
    msg = await interaction.followup.send(
        content=f"Successfully removed {hint_points} hint points from {user.mention}",
        embed=balance_embed,
        view=balance_view,
        allowed_mentions=AllowedMentions(everyone=False, roles=False, users=[user]),
    )
#===================================================
# ============= Subcommand "view" of "hint_points"
@hint_points_cmd.subcommand(
    name="view",
    description="View hint points.",
)
async def hint_points_view(
        interaction: Interaction,
        user: Member = SlashOption(
            description="The person whose hint points you want to view.",
            required=False,
            default=None
        ),
):
    await interaction.response.defer()
    if user is None:
        user = interaction.user
    else:
        if not interaction.user.id == OWNER_ID:
            if not user.id == interaction.user.id:
                await interaction.followup.send(f"You cannot look at other people's hint point banks.")
                return
    db = db_client.hint_points
    collection = db.balance
    existing_bank = await collection.find_one({"_id": str(user.id)})
    if existing_bank is None:
        await interaction.followup.send(no_bank_msg_for(user.mention))
        return
    existing_bank["history"].sort(key=lambda x: x["time"], reverse=True)
    history = paginate_list(existing_bank["history"])
    balance_view = BankBalanceViewer(0, existing_bank["balance"], history, user)
    balance_embed = format_balance_embed(balance_view)
    msg = await interaction.followup.send(
        embed=balance_embed,
        view=balance_view
    )
#===================================================
# ============= Subcommand "transfer" of "hint_points"
@hint_points_cmd.subcommand(
    description="Transfer hint points to another person.",
)
async def transfer(
        interaction: Interaction,
        user: Member = SlashOption(
            description="The person you want to send hint_points to.",
            required=True,
        ),
        hint_points: int = SlashOption(
            description="The number of hint_points you want to send.",
            required=True,
            min_value=1,
        ),
        reason: str = SlashOption(
            description="Why you sent these hint_points.",
            required=True,
        ),
):
    await interaction.response.defer()
    if user.id == interaction.user.id:
        await interaction.followup.send("You can't transfer hint points to yourself!")
        return
    db = db_client.hint_points
    collection = db.balance
    send_bank = await collection.find_one({"_id": str(interaction.user.id)})
    receive_bank = await collection.find_one({"_id": str(user.id)})
    if send_bank is None:
        await interaction.followup.send(no_bank_msg_for(user.mention))
        return
    if receive_bank is None:
        await interaction.followup.send(no_bank_msg_for(user.mention))
        return
    transaction_time = int(datetime.now().timestamp())
    send_balance = send_bank["balance"]
    if send_balance - hint_points < 0:
        await interaction.followup.send(f"You can't just go into debt. You only have {send_balance} hint points.")
        return
    send_history_entry = {
        "time": transaction_time,
        "change": -hint_points,
        "new_balance": send_balance - hint_points,
        "user_id": str(interaction.user.id),
        "reason": f"Transaction of {hint_points} hint points to {user.mention}\n\nReason: " + reason
    }
    send_bank["history"].append(send_history_entry)
    send_updates = {
        "balance": send_balance - hint_points,
        "history": send_bank["history"],
    }
    receive_balance = receive_bank["balance"]
    receive_history_entry = {
        "time": transaction_time,
        "change": hint_points,
        "new_balance": receive_balance + hint_points,
        "user_id": str(interaction.user.id),
        "reason": f"Transaction of {hint_points} hint points from {interaction.user.mention}\n\nReason: " + reason
    }
    receive_bank["history"].append(receive_history_entry)
    receive_updates = {
        "balance": receive_balance + hint_points,
        "history": receive_bank["history"],
    }
    await collection.update_one({"_id": str(user.id)}, {"$set": receive_updates})
    await collection.update_one({"_id": str(interaction.user.id)}, {"$set": send_updates})
    existing_bank = await collection.find_one({"_id": str(user.id)})
    existing_bank["history"].sort(key=lambda x: x["time"], reverse=True)
    history = paginate_list(existing_bank["history"])
    balance_view = BankBalanceViewer(0, existing_bank["balance"], history, user)
    balance_embed = format_balance_embed(balance_view)
    msg = await interaction.followup.send(
        content=f"Successfully transferred {hint_points} hint points to {user.mention}'s cell bank. You now have {send_balance - hint_points} hint points.",
        embed=balance_embed,
        view=balance_view,
        allowed_mentions=AllowedMentions(everyone=False, roles=False, users=[user]),
    )
#===================================================
# ============= Subcommand "list" of "hint_points"
@hint_points_cmd.subcommand(
    name="list",
    description="List the balances of everyone's hint point bank. Owner-only.",
)
async def hint_points_list(
        interaction: Interaction,
):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.id == OWNER_ID:
        await interaction.followup.send(f"Only Lunarisk can see everyone else's hint points. Go away.")
        return
    db = db_client.hint_points
    collection = db.balance
    banks = collection.find({})
    bank_message = ""
    async for bank in banks:
        bank_message += f"<@{bank['_id']}> {bank['balance']} hint points\n"
    bank_list_embed = Embed(
        title=f"Hint Point Banks",
        description=bank_message,
        colour=BOT_COLOUR,
    )
    await interaction.followup.send(
        embed=bank_list_embed
    )





#===================================================
# ============= "market" Group of Commands =============
@bot.slash_command(
    name="market",
    description="Market info & trading (view, purchase, sell, portfolio)",
    force_global=True
)
async def market_cmd(interaction: Interaction):
    # group root; no direct execution
    pass

# ---------- Public: see configurable items ----------
@market_cmd.subcommand(
    name="view",
    description="View current market items (A–H)."
)
@guard(require_private=True, public=True, allow_channel_ids=ALLOWED_MKVIEW_CHANNEL_IDS)
async def market_view(interaction: Interaction): 
    cfg = await _get_market_config()
    
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured yet.")
        return

    items = cfg["items"]
    use_next = bool(cfg.get("use_next_for_total"))  # set by /stock_change reveal_next
    caption = "Valuation uses NEXT-year prices" if use_next else "Valuation uses current prices"

    lines = []
    for code in ITEM_CODES:
        info = items.get(code, {})
        name = info.get("name", code)
        price = int(info.get("next_price" if use_next else "price", 0))
        lines.append(f"**{code}: {name}** — {price:,}")

    await interaction.followup.send(
        embed=Embed(
            title="Market Items (A–H)",
            description=f"*{caption}*\n\n" + "\n".join(lines),
            colour=BOT_COLOUR
        )
    )

# ---------- Personal Inventory ----------
@market_cmd.subcommand(name="inv", description="View your own Inventory.")
@guard(require_private=True, public=True)
async def market_portfolio(interaction: Interaction):
    # Load config & items
    cfg = await _get_market_config()
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured yet.")
        return
    items = cfg["items"]

    # Load portfolio
    uid = str(interaction.user.id)
    pf = await db_client.market.portfolios.find_one({"_id": uid})
    if not pf:
        await interaction.followup.send("❌ No Inventory. Use **/signup join** first.")
        return

    # Decide pricing mode (respect freeze)
    global_use_next = bool(cfg.get("use_next_for_total"))
    next_year = int(cfg.get("next_year")) if cfg.get("next_year") is not None else None
    use_next = global_use_next
    frozen_note = False
    if global_use_next and next_year is not None and pf.get("frozen_year") == next_year:
        # Quarantined for this NEXT result → value at current prices
        use_next = False
        frozen_note = True

    # Baselines
    _, total_current = _portfolio_totals_with_mode(items, pf, use_next=False)  # always current
    unspent, total_shown = _portfolio_totals_with_mode(items, pf, use_next=use_next)

    # Holdings list (valued in the chosen mode)
    holdings = pf.get("holdings", {}) or {}
    holdings_lines = []
    for code in ITEM_CODES:
        qty = int(holdings.get(code, 0))
        if qty <= 0:
            continue
        base = int(items[code]["price"])
        p = int(items[code].get("next_price", base)) if use_next else base
        value = qty * p
        holdings_lines.append(f"{code} - {items[code]['name']}: {qty} (≈ {value})")

    # 2-decimal change vs current when NEXT is shown
    change_line = ""
    if use_next:
        if total_current > 0:
            pct = (total_shown / total_current - 1.0) * 100.0
            change_line = f"\n**Change vs current:** {fmt_pct2_signed(pct)}"
        else:
            change_line = "\n**Change vs current:** N/A"

    # Description
    mode_label = "NEXT-year prices" if use_next else "current prices"
    frozen_line = f"\n_Valuation uses **CURRENT** prices (frozen for Y{next_year})._" if frozen_note else ""
    desc = (
        f"_Valuation uses {mode_label}_{frozen_line}\n\n"
        f"**Unspent Cash**: {unspent}\n"
        f"**Total Cash**: {total_shown}"
        f"{change_line}\n\n"
        + ("**Holdings**\n" + "\n".join(holdings_lines) if holdings_lines else "_No holdings_")
    )

    await interaction.followup.send(embed=Embed(
        title=f"Portfolio — {interaction.user.display_name}",
        description=desc,
        colour=BOT_COLOUR
    ))

# ---------- Owner-only: inspect someone else’s Inventory ----------
@market_cmd.subcommand(name="admin_inv", description="OWNER: View the inventory of a certain user.")
@guard(require_private=True, public=True, owner_only=True)
async def market_admin_view(
    interaction: Interaction,
    user: Member = SlashOption(description="User to view", required=True)
):
    # Load config & items
    cfg = await _get_market_config()
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured yet.")
        return
    items = cfg["items"]

    # Load portfolio
    uid = str(user.id)
    pf = await db_client.market.portfolios.find_one({"_id": uid})
    if not pf:
        await interaction.followup.send("❌ No Inventory for that user.")
        return

    # Decide pricing mode (respect freeze)
    global_use_next = bool(cfg.get("use_next_for_total"))
    next_year = int(cfg.get("next_year")) if cfg.get("next_year") is not None else None
    use_next = global_use_next
    frozen_note = False
    if global_use_next and next_year is not None and pf.get("frozen_year") == next_year:
        use_next = False
        frozen_note = True

    # Baselines
    _, total_current = _portfolio_totals_with_mode(items, pf, use_next=False)   # always current
    unspent, total_shown = _portfolio_totals_with_mode(items, pf, use_next=use_next)

    # Holdings list (valued in the chosen mode)
    holdings = pf.get("holdings", {}) or {}
    holdings_lines = []
    for code in ITEM_CODES:
        qty = int(holdings.get(code, 0))
        if qty <= 0:
            continue
        base = int(items[code]["price"])
        p = int(items[code].get("next_price", base)) if use_next else base
        value = qty * p
        name = items[code]["name"]
        holdings_lines.append(f"{code} - {name}: {qty} (≈ {value})")

    # 2-decimal change vs current when NEXT is shown
    change_line = ""
    if use_next:
        if total_current > 0:
            pct = (total_shown / total_current - 1.0) * 100.0
            change_line = f"\n**Change vs current:** {fmt_pct2_signed(pct)}"
        else:
            change_line = "\n**Change vs current:** N/A"

    # Description
    mode_label = "NEXT-year prices" if use_next else "current prices"
    frozen_line = f"\n_Valuation uses **CURRENT** prices (frozen for Y{next_year})._" if frozen_note else ""
    desc = (
        f"_Valuation uses {mode_label}_{frozen_line}\n\n"
        f"**Unspent Cash**: {unspent}\n"
        f"**Total Cash**: {total_shown}"
        f"{change_line}\n\n"
        + ("**Holdings**\n" + "\n".join(holdings_lines) if holdings_lines else "_No holdings_")
    )

    await interaction.followup.send(embed=Embed(
        title=f"Portfolio — {user.display_name}",
        description=desc,
        colour=BOT_COLOUR
    ))

# ---------- Buy: spend Unspent Cash to increase holdings ----------
@market_cmd.subcommand(
    name="buy",
    description="Buy items. Multiple pairs allowed (e.g., 'A 10, Apple 3')."
)
@guard(require_private=True, public=True, require_unlocked=True)
async def market_purchase(
    interaction: Interaction,
    orders: str = SlashOption(
        description="Pairs like 'A 10, C 5' or names 'Apple 3' (comma/|/;/newline separated).",
        required=True
    ),
):
    # Load market config
    cfg = await _get_market_config()
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured yet.")
        return
    items = cfg["items"]

    # Load caller portfolio
    uid = str(interaction.user.id)
    pf_col = db_client.market.portfolios
    pf = await pf_col.find_one({"_id": uid})
    if not pf:
        await interaction.followup.send("❌ No portfolio. Use **/signup join** first.")
        return
    pf["holdings"] = (pf.get("holdings") or {})  # defensive

    # ⛔ Elimination gate (self)
    if await _block_if_eliminated(interaction, uid):
        return
    
    # 🧊 Freeze gate (players cannot trade while quarantined for this NEXT year)
    use_next_global = bool(cfg.get("use_next_for_total"))
    next_year = int(cfg["next_year"]) if cfg.get("next_year") is not None else None
    if use_next_global and next_year is not None and pf.get("frozen_year") == next_year:
        await interaction.followup.send(
            f"🧊 You are currently **frozen for Y{next_year}**. The host is resolving your inventory."
        )
        return

    # Parse "(item, qty)" pairs
    try:
        pairs = _parse_orders(orders)  # must return list[tuple[str,int]]
    except ValueError as e:
        await interaction.followup.send(f"❌ {e}")
        return
    if not pairs:
        await interaction.followup.send("❌ No valid orders found.")
        return

    # Build add map & compute total cost; enforce per-item limits
    add_map: dict[str, int] = {}
    total_cost = 0
    for ident, qty in pairs:
        if qty <= 0:
            await interaction.followup.send("❌ Quantities must be positive integers.")
            return
        code = _resolve_item_code(items, ident)
        if not code:
            await interaction.followup.send(f"❌ Unknown item: `{ident}`")
            return
        price = int(items[code]["price"])
        cur_qty = int(pf["holdings"].get(code, 0))
        new_qty = cur_qty + add_map.get(code, 0) + qty
        if new_qty > MAX_ITEM_UNITS:
            await interaction.followup.send(
                f"❌ Holding limit exceeded for {code}. Max {MAX_ITEM_UNITS}."
            )
            return
        add_map[code] = add_map.get(code, 0) + qty
        total_cost += price * qty

    # Debt check
    cash_now = int(pf.get("cash", 0))
    if cash_now - total_cost < 0:
        await interaction.followup.send(
            f"❌ Not enough cash. Need {total_cost}, you have {cash_now} (debt not allowed)."
        )
        return

    # Elimination gate: only enforce in elimination mode
    if await _mode_is("elimination"):
        is_elim, elim_year = await _is_eliminated_user(str(interaction.user.id))  # self-trade
        if is_elim:
            await interaction.followup.send(
                "⛔ This portfolio has been **eliminated** and cannot trade (buy/sell)."
            )
            return
    
    # Apply updates (atomic per user doc)
    for code, qty in add_map.items():
        pf["holdings"][code] = int(pf["holdings"].get(code, 0)) + qty
    pf["cash"] = cash_now - total_cost
    await pf_col.update_one(
        {"_id": uid},
        {"$set": {"holdings": pf["holdings"], "cash": pf["cash"], "updated_at": _now_ts()}}
    )

    # Totals for receipt (respect freeze logic if NEXT is active)
    use_next = use_next_global
    if use_next_global and next_year is not None and pf.get("frozen_year") == next_year:
        use_next = False
    unspent, total = _portfolio_totals_with_mode(items, pf, use_next=use_next)

    # Reply summary
    lines = [f"{code} ({items[code]['name']}): +{qty} @ {items[code]['price']}"
             for code, qty in add_map.items()]
    await interaction.followup.send(
        "✅ Purchase complete:\n- " + "\n- ".join(lines) +
        f"\n**Unspent Cash**: {unspent}\n**Total Cash**: {total}"
    )

# ============= Subcommand "admin_buy" of "market" =============
@market_cmd.subcommand(name="admin_buy", description="OWNER: Purchase items for a player.")
@guard(require_private=True, public=True, require_unlocked=True, owner_only=True)
async def market_admin_purchase(
    interaction: Interaction,
    user: Member = SlashOption(description="Player to purchase for", required=True),
    orders: str = SlashOption(
        description="Pairs like 'A 10, C 5' or names 'Apple 3' (comma/|/; or newline separated).",
        required=True
    ),
):
    # Load config (and optional private category gate)
    cfg = await _get_market_config()
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured yet.")
        return
    items = cfg["items"]

    # Load target portfolio
    uid = str(user.id)
    pf_col = db_client.market.portfolios
    pf = await pf_col.find_one({"_id": uid})
    if not pf:
        await interaction.followup.send(
            f"❌ {user.mention} has no portfolio. They must **/signup join** first."
        )
        return

    # ⛔ Elimination gate (self)
    if await _block_if_eliminated(interaction, uid):
        return
    
    # Parse orders → [(identifier, qty)]
    try:
        pairs = _parse_admin_orders(orders)
    except ValueError as e:
        await interaction.followup.send(f"❌ {e}")
        return

    # Resolve identifiers to item codes and validate; price at CURRENT price
    adds: dict[str, int] = {}
    total_cost = 0
    holdings = dict(pf.get("holdings") or {})
    cash = int(pf.get("cash", 0))

    for ident, qty in pairs:
        code = _resolve_item_code(items, ident)
        if not code:
            await interaction.followup.send(f"❌ Unknown item: `{ident}`")
            return
        if qty <= 0:
            await interaction.followup.send("❌ Quantities must be positive integers.")
            return

        price = int(items[code]["price"])  # Classic: purchases use CURRENT price
        cur_qty = int(holdings.get(code, 0))
        if cur_qty + qty > MAX_ITEM_UNITS:
            await interaction.followup.send(
                f"❌ Holding limit exceeded for {code}. Max {MAX_ITEM_UNITS}."
            )
            return

        adds[code] = adds.get(code, 0) + qty
        total_cost += price * qty

    # Debt restriction
    if cash - total_cost < 0:
        await interaction.followup.send(
            f"❌ Not enough cash for {user.mention}. Needs {total_cost}, has {cash}."
        )
        return

    # Apply
    for code, qty in adds.items():
        holdings[code] = int(holdings.get(code, 0)) + qty
    cash -= total_cost

    await pf_col.update_one(
        {"_id": uid},
        {"$set": {"holdings": holdings, "cash": cash, "updated_at": _now_ts()}},
        upsert=True
    )

    # Receipt
    lines = [
        f"{code} — {items[code]['name']}: +{qty} @ {items[code]['price']} = {items[code]['price']*qty}"
        for code, qty in adds.items()
    ]
    await interaction.followup.send(
        f"✅ Purchased for {user.mention}:\n- " + "\n- ".join(lines) +
        f"\n**Remaining Unspent Cash**: {cash}"
    )

# ---------- Sell: convert holdings back to Unspent Cash ----------
@market_cmd.subcommand(
    name="sell",
    description="Sell items. Multiple pairs allowed; partial sell allowed."
)
@guard(require_private=True, public=True, require_unlocked=True)
async def market_sell(
    interaction: Interaction,
    orders: str = SlashOption(
        description="Pairs like 'A 10, C 5' or names 'Apple 3' (comma/|/;/newline separated).",
        required=True
    ),
):
    # Load market config
    cfg = await _get_market_config()
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured yet.")
        return
    items = cfg["items"]

    # Load caller portfolio
    uid = str(interaction.user.id)
    pf_col = db_client.market.portfolios
    pf = await pf_col.find_one({"_id": uid})
    if not pf:
        await interaction.followup.send("❌ No portfolio. Use **/signup join** first.")
        return
    pf["holdings"] = (pf.get("holdings") or {})
    pf["cash"] = int(pf.get("cash", 0))

    # ⛔ Elimination gate (self)
    if await _block_if_eliminated(interaction, uid):
        return
    
    # 🧊 Freeze gate (players cannot trade while quarantined for this NEXT year)
    use_next_global = bool(cfg.get("use_next_for_total"))
    next_year = int(cfg["next_year"]) if cfg.get("next_year") is not None else None
    if use_next_global and next_year is not None and pf.get("frozen_year") == next_year:
        await interaction.followup.send(
            f"🧊 You are currently **frozen for Y{next_year}**. The host is resolving your inventory."
        )
        return

    # Parse "(item, qty)" pairs
    try:
        pairs = _parse_orders(orders)  # -> list[tuple[str,int]]
    except ValueError as e:
        await interaction.followup.send(f"❌ {e}")
        return
    if not pairs:
        await interaction.followup.send("❌ No valid orders found.")
        return

    sold_lines: list[str] = []
    total_gain = 0
    any_success = False

    for ident, req_qty in pairs:
        if req_qty <= 0:
            sold_lines.append(f"❌ Invalid quantity for `{ident}` — must be positive.")
            continue

        code = _resolve_item_code(items, ident)
        if not code:
            sold_lines.append(f"❌ Unknown item: `{ident}` — skipped")
            continue

        have = int(pf["holdings"].get(code, 0))
        if have <= 0:
            sold_lines.append(f"❌ {code} ({items[code]['name']}): you have 0 — rejected")
            continue

        # Partial sell allowed: sell up to what the user has
        sell_qty = min(have, req_qty)
        price = int(items[code]["price"])  # sells refund **base (current) price**
        gain = sell_qty * price

        pf["holdings"][code] = have - sell_qty
        pf["cash"] += gain
        total_gain += gain
        any_success = True

        if sell_qty < req_qty:
            sold_lines.append(
                f"⚠️ {code} ({items[code]['name']}): requested {req_qty}, sold {sell_qty} (all you had) @ {price}"
            )
        else:
            sold_lines.append(f"✅ {code} ({items[code]['name']}): -{sell_qty} @ {price}")

    if any_success:
        await pf_col.update_one(
            {"_id": uid},
            {"$set": {"holdings": pf["holdings"], "cash": pf["cash"], "updated_at": _now_ts()}}
        )

    # Totals for the receipt (respect freeze logic if NEXT is active)
    use_next = use_next_global
    if use_next_global and next_year is not None and pf.get("frozen_year") == next_year:
        use_next = False
    unspent, total = _portfolio_totals_with_mode(items, pf, use_next=use_next)

    summary = "\n- ".join(sold_lines) if sold_lines else "(nothing parsed)"
    await interaction.followup.send(
        f"{summary}\n\n**Unspent Cash**: {unspent} (+{total_gain})\n**Total Cash**: {total}"
    )

# ============= Subcommand "admin_sell" of "market" (Classic only) =============
@market_cmd.subcommand(name="admin_sell",description="OWNER: Sell items from a player's holdings (refund at current price).")
@guard(require_private=True, public=True, require_unlocked=True, owner_only=True)
async def market_admin_sell(
    interaction: Interaction,
    user: Member = SlashOption(description="Player to sell for", required=True),
    orders: str = SlashOption(
        description="Pairs like 'A 10, C 5' or names 'Apple 3' (comma/|/; or newline separated).",
        required=True
    ),
):
    # Load config/items
    cfg = await _get_market_config()
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured yet.")
        return
    items = cfg["items"]

    # Load target portfolio
    uid = str(user.id)
    pf_col = db_client.market.portfolios
    pf = await pf_col.find_one({"_id": uid})
    if not pf:
        await interaction.followup.send(f"❌ {user.mention} has no portfolio. They must **/signup join** first.")
        return

    # ⛔ Elimination gate (self)
    if await _block_if_eliminated(interaction, uid):
        return
    
    # Parse orders
    try:
        pairs = _parse_admin_orders(orders)   # re-use the helper from admin_purchase
    except ValueError as e:
        await interaction.followup.send(f"❌ {e}")
        return

    holdings = dict(pf.get("holdings") or {})
    cash = int(pf.get("cash", 0))

    # Build sell plan (partial allowed: sell up to owned; zero owned → skip with note)
    proceeds = 0
    sold: dict[str, int] = {}
    skipped: list[str] = []

    for ident, req_qty in pairs:
        code = _resolve_item_code(items, ident)
        if not code:
            await interaction.followup.send(f"❌ Unknown item: `{ident}`")
            return
        if req_qty <= 0:
            await interaction.followup.send("❌ Quantities must be positive integers.")
            return

        owned = int(holdings.get(code, 0))
        if owned <= 0:
            skipped.append(f"{code} — {items[code]['name']}: none owned")
            continue

        sell_qty = req_qty if req_qty <= owned else owned  # partial sell if not enough
        price = int(items[code]["price"])                   # Classic: current price
        proceeds += sell_qty * price
        holdings[code] = owned - sell_qty
        sold[code] = sold.get(code, 0) + sell_qty

    if not sold and skipped:
        await interaction.followup.send("ℹ️ Nothing sold: " + "; ".join(skipped))
        return

    cash += proceeds
    await pf_col.update_one(
        {"_id": uid},
        {"$set": {"holdings": holdings, "cash": cash, "updated_at": _now_ts()}},
        upsert=True
    )

    # Receipt (public)
    lines = [f"{code} — {items[code]['name']}: -{qty} @ {items[code]['price']} = {items[code]['price']*qty}"
             for code, qty in sold.items()]
    extra = ("\n\nSkipped: " + "; ".join(skipped)) if skipped else ""
    await interaction.followup.send(
        f"✅ Sold for {user.mention}:\n- " + "\n- ".join(lines) +
        f"\n**Unspent Cash**: {cash}{extra}"
    )
# ---------- Lock: Admin only command to temporarily lock purchases & Hint Point usage. ----------
@market_cmd.subcommand(
    name="lock_trading",
    description="OWNER: Temporarily lock all trading (buy/sell/admin purchase)."
)
async def market_lock_trading(interaction: Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("❌ Owner only.")
        return
    await interaction.response.defer(ephemeral=True)

    if await _trading_locked():
        await interaction.followup.send("ℹ️ Trading and hints are already **locked**.")
        return

    await _set_trading_locked(True)
    await interaction.followup.send("🔒 Trading and hints are now **locked**. Buying and selling are disabled.")

@market_cmd.subcommand(
    name="unlock_trading",
    description="OWNER: Unlock trading."
)
async def market_unlock_trading(interaction: Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("❌ Owner only.")
        return
    await interaction.response.defer(ephemeral=True)

    if not await _trading_locked():
        await interaction.followup.send("ℹ️ Trading and hints are already **unlocked**.")
        return

    await _set_trading_locked(False)
    await interaction.followup.send("🔓 Trading and hints are now **unlocked**.")






# ============= "stock_change" Group of Commands (modernized) =============
@bot.slash_command(
    name="stock_change",
    description="Owner: manage yearly stock % changes and reveal next-year projection",
    force_global=True
)
async def stock_change_cmd(interaction: Interaction):
    # group root; no direct execution
    pass

# ---------- Utilities ----------
async def _get_changes_for_year(year: int) -> dict | None:
    """Return {A..H: percent} for a year from db.stocks.changes, or None."""
    doc = await db_client.stocks.changes.find_one({"_id": year})
    if not doc:
        return None
    return {k: int(v) for k, v in doc.items() if k in ITEM_CODES}

def _price_with_change(base_price: int, percent: int) -> int:
    """100% => 2x; -50% => 0.5x; round to int; never negative."""
    return max(0, int(round(base_price * (1.0 + percent / 100.0))))

# ---------- /stock_change set (mode-aware, uses ODDS / ODDS_APOC keys) ----------
@stock_change_cmd.subcommand(
    name="set",
    description="Owner: set 8 % changes (A–H) for a year. Example: -10 5 0 40 -20 0 10 15"
)
async def stock_change_set(
    interaction: Interaction,
    changes: str = SlashOption(
        description="8 integers separated by a space (e.g., -10 5 0 40 -20 0 10 15)",
        required=True,
    ),
    year: int = SlashOption(
        description="Year the changes apply to",
        required=True,
    ),
):
    await interaction.response.defer(ephemeral=True)

    # Owner gate
    if interaction.user.id != OWNER_ID:
        await interaction.followup.send("❌ Owner only.")
        return

    # Parse inputs
    parts = [p.strip() for p in changes.split()]
    if len(parts) != 8:
        await interaction.followup.send("❌ Provide exactly **8** changes separated by spaces.")
        return
    try:
        vals = [int(x) for x in parts]
    except ValueError:
        await interaction.followup.send("❌ All changes must be integers (e.g., -10, 0, 25).")
        return

    # Allowed domain from the active mode's ODDS table
    mode = await _get_game_mode()
    allowed = set(ODDS_APOC.keys()) if mode == "apocalypse" else set(ODDS.keys())

    bad = [v for v in vals if v not in allowed]
    if bad:
        await interaction.followup.send(
            ("⛔ In **Apocalypse** mode, **no positive changes** are allowed.\n" if mode == "apocalypse" else "") +
            f"Disallowed values: {sorted(set(bad))}\n"
            f"Allowed set: {sorted(allowed)}"
        )
        return

    # Upsert
    payload = {"_id": int(year)}
    for i, code in enumerate(ITEM_CODES):
        payload[code] = vals[i]

    col = db_client.stocks.changes
    existed = await col.find_one({"_id": int(year)}) is not None
    if existed:
        await col.delete_one({"_id": int(year)})
    await col.insert_one(payload)

    preview = ", ".join([f"{ITEM_CODES[i]}:{vals[i]}%" for i in range(8)])
    note = "\nNote: Replaced existing year." if existed else ""
    await interaction.followup.send(
        f"✅ Recorded stock changes for **{year}** (mode: **{mode}**) → {preview}{note}"
    )

# ---------- /stock_change view ----------
@stock_change_cmd.subcommand(
    name="view",
    description="Owner: view stock changes for a year.",
)
async def stock_change_view(
    interaction: Interaction,
    year: int = SlashOption(
        description="Year to view",
        required=True,
        autocomplete_callback=year_autocomplete
    ),
):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != OWNER_ID:
        await interaction.followup.send("❌ Owner only.")
        return

    changes_doc = await _get_changes_for_year(year)
    if not changes_doc:
        await interaction.followup.send("❌ This year doesn't exist in the database.")
        return

    # Pull current market item names; fall back gracefully if not configured
    cfg = await _get_market_config()
    items_cfg = (cfg or {}).get("items", {})

    # Show 'A — Apple: +10%' style lines
    lines = [
        f"{_item_label(code, items_cfg)}: {'+' if changes_doc[code] > 0 else ''}{changes_doc[code]}%"
        for code in ITEM_CODES
    ]

    await interaction.followup.send(
        embed=Embed(
            title=f"Year {year} — Changes",
            description="\n".join(lines),
            colour=BOT_COLOUR
        )
    )

# ---------- /stock_change odds ----------
@stock_change_cmd.subcommand(
    name="odds",
    description="Owner: compute odds from historical changes."
)
async def stock_change_odds(interaction: Interaction):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != OWNER_ID:
        await interaction.followup.send("❌ Owner only.")
        return

    years = [doc async for doc in db_client.stocks.changes.find({})]
    if not years:
        await interaction.followup.send(
            "There is no stock info in the database.\n"
            "Either you did not set changes, or this is the start of a season (all 50%)."
        )
        return

    years.sort(key=lambda d: d["_id"])
    r_years = years[:-1]  # R-hint excludes latest year

    items_cfg = (await _get_market_config() or {}).get("items", {})

    r_odds_info = ""
    if r_years:
        r_odds = calculate_odds(r_years)
        r_odds_info = "R-hint (excludes latest year changes)\n\n" + "\n".join(
            f"{_item_label(k, items_cfg)}: {v}%"
            for k, v in r_odds.items()
        )

    owner_odds = calculate_odds(years)
    owner_odds_info = "Owner odds (includes latest year)\n\n" + "\n".join(
        f"{_item_label(k, items_cfg)}: {v}%"
        for k, v in owner_odds.items()
    )

    await interaction.followup.send((r_odds_info + ("\n\n" if r_odds_info else "") + owner_odds_info) or "No data.")

# ---------- /stock_change reveal_next ----------
@stock_change_cmd.subcommand(
    name="reveal_next",
    description="Owner: project next-year prices from a set year and switch portfolio totals to NEXT."
)
async def stock_change_reveal_next(
    interaction: Interaction,
    year: int = SlashOption(description="Year to project", required=True),
    confirm: str = SlashOption(description="Type CONFIRM to proceed.", required=True),
):
    await interaction.response.defer(ephemeral=True)
    if interaction.user.id != OWNER_ID:
        await interaction.followup.send("❌ Owner only.")
        return
    if confirm != "CONFIRM":
        await interaction.followup.send("❌ Type `CONFIRM` to proceed.")
        return

    # Require changes for the chosen year
    ch = await _get_changes_for_year(year)
    if not ch:
        await interaction.followup.send(f"❌ No changes found for {year}. Set them first with /stock_change set.")
        return

    # Load current items and compute next_price for each A..H
    cfg_col = db_client.market.config
    cfg = await cfg_col.find_one({"_id": "current"})
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured.")
        return

    pre_id = await _snapshot_pre_reveal(int(year))
    items = cfg["items"]
    for code in ITEM_CODES:
        base_price = int(items[code]["price"])
        pct = int(ch.get(code, 0))
        items[code]["next_price"] = _price_with_change(base_price, pct)

    # Flip the flag so portfolio totals use next_price (Unspent Cash unchanged)
    await cfg_col.update_one(
        {"_id": "current"},
        {"$set": {"items": items, "use_next_for_total": True, "next_year": int(year), "updated_at": int(datetime.now().timestamp())}}
    )

    preview = "\n".join([f"{c}: {items[c]['name']} — {items[c]['price']} → **{items[c]['next_price']}** ({ch.get(c,0)}%)"
                         for c in ITEM_CODES])
    await interaction.followup.send(embed=Embed(
        title=f"Next-Year Revealed — {year}",
        description=preview,
        colour=BOT_COLOUR
    ))

# ============= Subcommand "liquidate" of "stock_change" =============
@stock_change_cmd.subcommand(
    name="liquidate",
    description="Owner: liquidate holdings into Unspent at the currently shown prices."
)
async def stock_change_liquidate(
    interaction: Interaction,
    confirm: str = SlashOption(description="Type CONFIRM to proceed.", required=True),
):
    await interaction.response.defer(ephemeral=True)

    # Owner check
    if interaction.user.id != OWNER_ID:
        await interaction.followup.send("❌ Owner only.")
        return

    # Safety check
    if confirm != "CONFIRM":
        await interaction.followup.send("❌ Type `CONFIRM` to proceed.")
        return

    # Load config & items
    cfg = await db_client.market.config.find_one({"_id": "current"})
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured.")
        return
    items = cfg["items"]
    use_next = bool(cfg.get("use_next_for_total"))

    # Result year (must be set by reveal step)
    result_year = int(cfg.get("next_year")) if "next_year" in cfg else None
    if result_year is None:
        await interaction.followup.send("❌ Cannot liquidate: no `next_year` set. Reveal prices first.")
        return

    # Persist 'last_result_year' marker for downstream logic
    await db_client.market.config.update_one(
        {"_id": "current"},
        {"$set": {"last_result_year": int(result_year), "updated_at": _now_ts()}},
        upsert=True
    )

    # Determine game mode (apocalypse burns unspent cash)
    mode = await _get_game_mode()
    is_apoc = (mode == "apocalypse")

    # Snapshot BEFORE mutating anything
    _ = await _snapshot_state_for_liquidate(result_year)

    portfolios = db_client.market.portfolios
    zero_holdings = {code: 0 for code in ITEM_CODES}

    modified = 0
    skipped = 0
    total_burned = 0  # apocalypse-only metric

    async for pf in portfolios.find({}):
        # If valuing at NEXT prices, skip quarantined portfolios for this year
        if use_next and pf.get("frozen_year") == result_year:
            skipped += 1
            continue

        # Compute totals under valuation mode
        unspent, total_at_mode = _portfolio_totals_with_mode(items, pf, use_next=use_next)

        # Apocalypse rule: burn any unspent cash; survivors carry only holdings' value
        if is_apoc:
            holdings_value_only = int(total_at_mode) - int(unspent)
            if holdings_value_only < 0:
                holdings_value_only = 0  # paranoia guard
            burned = int(unspent)
            total_burned += max(0, burned)
            new_cash = holdings_value_only
        else:
            # Classic/Elimination: carry total (unspent + holdings)
            new_cash = int(total_at_mode)

        # Liquidate: holdings -> 0; cash -> new_cash
        res = await portfolios.update_one(
            {"_id": pf["_id"]},
            {"$set": {
                "cash": new_cash,
                "holdings": zero_holdings,
                "updated_at": _now_ts()
            }}
        )
        modified += res.modified_count

    # Keep only the newest snapshot
    purged = await _purge_snapshots(keep_last=1)

    # Compose summary
    mode_label = "NEXT-year prices" if use_next else "current prices"
    skipped_line = f"\nSkipped (frozen): **{skipped}**." if use_next else ""
    apoc_line = (f"\n🔥 Apocalypse: **burned total Unspent** = {total_burned}."
                 if is_apoc else "")

    await interaction.followup.send(
        "✅ Liquidated holdings for "
        f"**{modified}** portfolios at **{mode_label}**."
        f"{skipped_line}\n"
        f"All holdings set to 0; players' **Unspent Cash** now equals "
        f"{'their holdings value only (Unspent burned)' if is_apoc else 'their Total Cash'}."
        f"\n🧹 Snapshot housekeeping: purged **{purged}** older snapshot(s)."
        f"{apoc_line}"
    )

# ============= Subcommand "revert" of "stock_change" =============
@stock_change_cmd.subcommand(
    name="revert",
    description="Owner: revert to the latest liquidation snapshot (config + portfolios)."
)
async def stock_change_revert(
    interaction: Interaction,
    confirm: str = SlashOption(description="Type REVERT to proceed.", required=True),
):
    await interaction.response.defer(ephemeral=True)

    if interaction.user.id != OWNER_ID:
        await interaction.followup.send("❌ Owner only.")
        return
    if confirm != "REVERT":
        await interaction.followup.send("❌ Type `REVERT` to proceed.")
        return

    # 1) Load the latest liquidate snapshot
    snap = await _snap_col().find_one(
        {"type": "liquidate"},
        sort=[("created_at", -1)]
    )
    if not snap:
        await interaction.followup.send("❌ No liquidation snapshot found to revert to.")
        return

    # 2) Restore market config (unset NEXT flags & remove next_price)
    cfg_col = db_client.market.config
    cfg = await cfg_col.find_one({"_id": "current"}) or {"_id": "current", "items": {}}
    items = cfg.get("items", {})

    # Remove any next_price keys
    for code in list(items.keys()):
        if isinstance(items[code], dict) and "next_price" in items[code]:
            items[code].pop("next_price", None)

    # Flip flags OFF
    await cfg_col.update_one(
        {"_id": "current"},
        {"$set": {"items": items, "use_next_for_total": False, "updated_at": _now_ts()},
         "$unset": {"next_year": ""}},
        upsert=True
    )

    # 3) Restore portfolios (cash/holdings) from snapshot
    restored = 0
    pf_col = db_client.market.portfolios
    for p in snap.get("portfolios", []):
        doc = {
            "_id": p["_id"],
            "cash": int(p.get("cash", 0)),
            "holdings": p.get("holdings", {}) or {},
            "updated_at": _now_ts(),
        }
        # Clear any freeze unless it was present in the snapshot
        if "frozen_year" in p:
            doc["frozen_year"] = p["frozen_year"]
        else:
            doc.pop("frozen_year", None)
        await pf_col.replace_one({"_id": p["_id"]}, doc, upsert=True)
        restored += 1

    await interaction.followup.send(
        f"↩️ Reverted to latest snapshot.\n"
        f"- Restored portfolios: **{restored}**\n"
        f"- NEXT pricing disabled; `next_year` cleared; `next_price` removed."
    )

#===================================================





#===================================================
# ============= General "use_hint" Group of Commands
@bot.slash_command(
    name="use_hint",
    description="Use your hint points",
    force_global=True)
async def use_hint(interaction: Interaction):
    pass
#===================================================
@use_hint.subcommand(name="r", description="Reveal odds of all stocks. Costs 1 HP.")
@guard(require_private=True, public=True, require_unlocked=True)
@disallow_self_hint_when_eliminated(public=True)
async def r_hint(
    interaction: Interaction,
    confirm: str = SlashOption(description="put R HINT in this to proceed.", required=True),
):
    if await _mode_is("apocalypse"):
        await interaction.followup.send("⛔ R-hint is **disabled** in Apocalypse mode.")
        return

    if confirm != "R HINT":
        await interaction.followup.send("Command rejected. Put ``R HINT`` in the ``confirm`` option to use an r hint.")
        return

    if await _trading_locked():
        await interaction.followup.send("❌ Hint usage is temporarily locked by the host.")
        return

    hint_collection = db_client.hint_points.balance
    hint_bank = await hint_collection.find_one({"_id": str(interaction.user.id)})
    if hint_bank is None:
        await interaction.followup.send(_signup_needed_msg_self())
        return
    hint_bank.setdefault("history", [])  # safety

    if int(hint_bank.get("balance", 0)) < 1:
        hint_bank["history"].sort(key=lambda x: x["time"], reverse=True)
        history = paginate_list(hint_bank["history"])
        balance_view = BankBalanceViewer(0, hint_bank["balance"], history, interaction.user)
        await interaction.followup.send(
            f"You need 1 hint point to use an R hint. You only have {hint_bank.get('balance', 0)} hint points.",
            embed=format_balance_embed(balance_view),
            view=balance_view,
        )
        return

    # All years except the latest (R-hint rule)
    collection = db_client.stocks.changes
    years = [doc async for doc in collection.find({})]
    years.sort(key=lambda y: y['_id'])
    years = years[:-1]
    if not years:
        await interaction.followup.send(
            "There is no stock info in this bot's database.\n"
            "Either the host did not update stock info, or this is the start of a season (All stocks 50%)."
        )
        return

    odds = calculate_odds(years)

    # Deduct 1 HP
    hint_bank["balance"] = int(hint_bank["balance"]) - 1
    hint_bank["history"].append({
        "time": int(datetime.now().timestamp()),
        "change": -1,
        "new_balance": hint_bank["balance"],
        "user_id": str(bot.user.id),
        "reason": "Used R-hint."
    })
    await hint_collection.update_one({"_id": str(interaction.user.id)}, {"$set": {
        "balance": hint_bank["balance"],
        "history": hint_bank["history"],
    }})

    # Pretty print (A..H order)
    items_cfg = (await _get_market_config() or {}).get("items", {})
    odd_lines = [f"{_item_label(code, items_cfg)}: {odds.get(code, 50)}%" for code in "ABCDEFGH"]

    hint_bank["history"].sort(key=lambda x: x["time"], reverse=True)
    history = paginate_list(hint_bank["history"])
    balance_view = BankBalanceViewer(0, hint_bank["balance"], history, interaction.user)

    await interaction.followup.send(
        "Used R-hint!\n\n" + "\n".join(odd_lines),
        embed=format_balance_embed(balance_view),
        view=balance_view,
    )
#===================================================
@use_hint.subcommand(name="lvl1", description="Reveals the strength of change for a single stock. Costs 1 HP.")
@guard(require_private=True, public=True, require_unlocked=True)
@disallow_self_hint_when_eliminated(public=True)
async def lvl1_hint(
    interaction: Interaction,
    stock: str = SlashOption(description="Stock", required=True, choices=[s for s in "ABCDEFGH"]),
    confirm: str = SlashOption(description="Type LVL1 to proceed.", required=True),
):
    if confirm != "LVL1":
        await interaction.followup.send("Command rejected. Put ``LVL1`` in the ``confirm`` option to use a hint.")
        return

    # Global lock
    if await _trading_locked():
        await interaction.followup.send("❌ Hint usage is temporarily locked by the host.")
        return

    # Bank
    col = db_client.hint_points.balance
    bank = await col.find_one({"_id": str(interaction.user.id)})
    if bank is None:
        await interaction.followup.send(_signup_needed_msg_self()); return

    items_cfg = (await _get_market_config() or {}).get("items", {})
    label = _item_label(stock, items_cfg)

    # ---------------- Gamemode Selector ----------------
    if await _mode_is("apocalypse"):
        # Apocalypse
        docs = [d async for d in db_client.stocks.changes.find({}, {"_id": 1, stock: 1})]
        docs.sort(key=lambda d: d["_id"])
        hist = docs[:-1] if docs else []
        prob = 50
        for y in hist:
            try:
                prob += int(ODDS_APOC.get(int(y.get(stock, 0)), 0))
            except Exception:
                pass
        prob = max(0, min(100, prob))
        msg = f"Used level 1 hint!\n\n**Chance of LOW fall** for {label}: **{prob}%**"
        cost = 1
    else:
        # Classic/Elim
        years = [doc async for doc in db_client.stocks.changes.find({})]
        years.sort(key=lambda y: y['_id'])
        if not years:
            await interaction.followup.send("There is no stock info in this bot's database."); return
        latest = years[-1]
        odds_change = int(ODDS[int(latest[stock])])
        strength = "**Low**" if abs(odds_change) <= 3 else ("**Medium**" if abs(odds_change) <= 9 else "**High**")
        msg = f"Used level 1 hint!\n\nChange of {label}: {strength}"
        cost = 1

    # All Gamemodes
    bal = int(bank.get("balance", 0))
    if bal < cost:
        bank["history"].sort(key=lambda x: x["time"], reverse=True)
        view = BankBalanceViewer(0, bank["balance"], paginate_list(bank["history"]), interaction.user)
        await interaction.followup.send(
            f"You need {cost} hint point(s). You only have {bal}.",
            embed=format_balance_embed(view), view=view
        ); return

    bank["balance"] = bal - cost
    bank["history"].append({"time": _now_ts(), "change": -cost, "new_balance": bank["balance"],
                            "user_id": str(bot.user.id), "reason": f"Used level 1 hint on {stock}."})
    await col.update_one({"_id": str(interaction.user.id)}, {"$set": {"balance": bank["balance"], "history": bank["history"]}})
    bank["history"].sort(key=lambda x: x["time"], reverse=True)
    view = BankBalanceViewer(0, bank["balance"], paginate_list(bank["history"]), interaction.user)
    await interaction.followup.send(msg, embed=format_balance_embed(view), view=view)
#===================================================
@use_hint.subcommand(name="lvl2", description="Gives 2 possible changes for a stock. Costs 2 HP")
@guard(require_private=True, public=True, require_unlocked=True)
@disallow_self_hint_when_eliminated(public=True)
async def lvl2_hint(
    interaction: Interaction,
    stock: str = SlashOption(description="Stock", required=True, choices=[s for s in "ABCDEFGH"]),
    confirm: str = SlashOption(description="Type LVL2 to proceed.", required=True),
):
    if confirm != "LVL2":
        await interaction.followup.send("Command rejected. Put ``LVL2`` in the ``confirm`` option to use a hint.")
        return
    if await _trading_locked():
        await interaction.followup.send("❌ Hint usage is temporarily locked by the host.")
        return

    col = db_client.hint_points.balance
    bank = await col.find_one({"_id": str(interaction.user.id)})
    if bank is None:
        await interaction.followup.send(_signup_needed_msg_self()); return

    items_cfg = (await _get_market_config() or {}).get("items", {})
    label = _item_label(stock, items_cfg)

    if await _mode_is("apocalypse"): # Apocalypse
        docs = [d async for d in db_client.stocks.changes.find({}, {"_id": 1, stock: 1})]
        docs.sort(key=lambda d: d["_id"])
        if not docs or stock not in docs[-1]:
            await interaction.followup.send("No stock info in this bot's database."); return
        change = int(docs[-1][stock])
        bucket = _apoc_bucket(change).upper()
        msg = f"Used level 2 hint!\n\n**Fall strength for {label}: {bucket}**"
        cost = 2
    else:
        # Classic/Elimination
        years = [doc async for doc in db_client.stocks.changes.find({})]
        years.sort(key=lambda y: y['_id'])
        latest = years[-1]
        stock_change = int(latest[stock])
        odds_change = int(ODDS[stock_change])
        opposite = next((int(c) for c, oc in ODDS.items() if int(oc) == -odds_change), stock_change)
        a, b = sorted([stock_change, opposite])
        msg = f"Used level 2 hint!\n\nPossible changes for {label}: **{a}%, {b}%**"
        cost = 2

    bal = int(bank.get("balance", 0))
    if bal < cost:
        bank["history"].sort(key=lambda x: x["time"], reverse=True)
        view = BankBalanceViewer(0, bank["balance"], paginate_list(bank["history"]), interaction.user)
        await interaction.followup.send(
            f"You need {cost} hint point(s). You only have {bal}.",
            embed=format_balance_embed(view), view=view
        ); return

    bank["balance"] = bal - cost
    bank["history"].append({"time": _now_ts(), "change": -cost, "new_balance": bank["balance"],
                            "user_id": str(bot.user.id), "reason": f"Used level 2 hint on {stock}."})
    await col.update_one({"_id": str(interaction.user.id)}, {"$set": {"balance": bank["balance"], "history": bank["history"]}})
    bank["history"].sort(key=lambda x: x["time"], reverse=True)
    view = BankBalanceViewer(0, bank["balance"], paginate_list(bank["history"]), interaction.user)
    await interaction.followup.send(msg, embed=format_balance_embed(view), view=view)
#===================================================
@use_hint.subcommand(name="lvl3", description="Shows whether a stock will increase or decrease. Costs 3 HP")
@guard(require_private=True, public=True, require_unlocked=True)
@disallow_self_hint_when_eliminated(public=True)
async def lvl3_hint(
    interaction: Interaction,
    stock: str = SlashOption(description="Stock", required=True, choices=[s for s in "ABCDEFGH"]),
    confirm: str = SlashOption(description="Type LVL3 to proceed.", required=True),
):
    if confirm != "LVL3":
        await interaction.followup.send("Command rejected. Put ``LVL3`` in the ``confirm`` option to use a hint.")
        return
    if await _trading_locked():
        await interaction.followup.send("❌ Hint usage is temporarily locked by the host.")
        return

    col = db_client.hint_points.balance
    bank = await col.find_one({"_id": str(interaction.user.id)})
    if bank is None:
        await interaction.followup.send(_signup_needed_msg_self()); return

    items_cfg = (await _get_market_config() or {}).get("items", {})
    label = _item_label(stock, items_cfg)

    if await _mode_is("apocalypse"): # Apocalypse
        docs = [d async for d in db_client.stocks.changes.find({}, {"_id": 1, stock: 1})]
        docs.sort(key=lambda d: d["_id"])
        if not docs or stock not in docs[-1]:
            await interaction.followup.send("There is no stock info in this bot's database."); return
        change = int(docs[-1][stock])
        msg = f"Used level 3 hint!\n\n**Exact fall for {label}: {change}%**"
        cost = 3
    else:
        # Classic/Elimination
        years = [doc async for doc in db_client.stocks.changes.find({})]
        years.sort(key=lambda y: y['_id'])
        latest = years[-1]
        v = int(latest[stock])
        if v > 0:  info = f"{label} will **increase**"
        elif v < 0: info = f"{label} will **decrease**"
        else:      info = f"{label} will **not change in price**"
        msg = "Used level 3 hint!\n\n" + info
        cost = 3

    bal = int(bank.get("balance", 0))
    if bal < cost:
        bank["history"].sort(key=lambda x: x["time"], reverse=True)
        view = BankBalanceViewer(0, bank["balance"], paginate_list(bank["history"]), interaction.user)
        await interaction.followup.send(
            f"You need {cost} hint point(s). You only have {bal}.",
            embed=format_balance_embed(view), view=view
        ); return

    bank["balance"] = bal - cost
    bank["history"].append({"time": _now_ts(), "change": -cost, "new_balance": bank["balance"],
                            "user_id": str(bot.user.id), "reason": f"Used level 3 hint on {stock}."})
    await col.update_one({"_id": str(interaction.user.id)}, {"$set": {"balance": bank["balance"], "history": bank["history"]}})
    bank["history"].sort(key=lambda x: x["time"], reverse=True)
    view = BankBalanceViewer(0, bank["balance"], paginate_list(bank["history"]), interaction.user)
    await interaction.followup.send(msg, embed=format_balance_embed(view), view=view)
#===================================================
# Elimination Gamemode ONLY

# ---------- Admin: full cash ranking (public) ----------
@market_cmd.subcommand(
    name="cash_rank",
    description="OWNER: Show full cash ranking of all players (public)."
)
@guard(require_private=False, public=True, owner_only=True)  # ← 공개 채널에서
@requires_mode("elimination", public=True)
async def market_cash_rank(interaction: Interaction):
    # Public only: block DMs
    if interaction.guild is None:
        # reply ephemerally in DM so we don't leak details
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("❌ This command must be used in a **server channel** (not in DMs).")
        return

    # Defer once (public)
    if not interaction.response.is_done():
        await interaction.response.defer()

    # Load portfolios
    portfolios = [pf async for pf in db_client.market.portfolios.find({})]
    if not portfolios:
        await interaction.followup.send("No portfolios found.")
        return

    # Sort by cash desc; annotate eliminated
    portfolios.sort(key=lambda p: int(p.get("cash", 0)), reverse=True)
    lines = []
    for i, p in enumerate(portfolios, 1):
        uid = p["_id"]
        eliminated = bool(p.get("eliminated"))
        cash = int(p.get("cash", 0))
        tag = " ⛔ ELIM" if eliminated else ""
        lines.append(f"{i}. <@{uid}> — {cash}{tag}")

    embed = Embed(
        title="Full Cash Ranking (All Players)",
        description="\n".join(lines),
        colour=BOT_COLOUR
    )
    await interaction.followup.send(embed=embed)

@bot.slash_command(
    name="elim_cut",
    description="OWNER: Preview & confirm the 3 eliminations for this result (DB 5~10). Public."
)
@guard(require_private=True, public=True, owner_only=True)
@requires_mode("elimination", public=True)   # other modes -> block with notice
async def elimination_cut(interaction: Interaction):

    # Check result year window: DB 5~10 == 4th~9th result
    ry = await _current_result_year()
    if ry is None:
        await interaction.followup.send("❌ No `last_result_year` recorded yet. Run liquidation first.")
        return
    if not (5 <= ry <= 10):
        await interaction.followup.send(f"⛔ Eliminations run only for DB 5~10. Current DB={ry}.")
        return

    # Prevent duplicate cut for same year
    already = await db_client.market.portfolios.count_documents({"elim_year": int(ry)})
    if already > 0:
        await interaction.followup.send(f"⛔ Eliminations for DB {ry} already executed.")
        return

    # Select bottom 3 (preview)
    candidates = await _bottom_three_survivors()
    if len(candidates) < 3:
        await interaction.followup.send("❌ Not enough survivors to eliminate 3 players.")
        return

    # Pretty list
    lines = [f"- <@{uid}> — {cash}" for uid, cash in candidates]
    # Map DB year to “Nth result”: DB 5 == 4th result → N = ry - 1
    nth = ry - 1

    embed = Embed(
        title=f"Elimination Preview — DB {ry} (Result #{nth})",
        description=(
            "The following players are the **bottom 3 by unspent cash** and will be eliminated.\n"
            + "\n".join(lines) +
            "\n\nPress **Confirm Cut** to finalize.\n"
            "_Once executed, eliminated portfolios cannot buy/sell (admin override disabled)._"
        ),
        colour=BOT_COLOUR
    )

    class ElimCutView(View):
        def __init__(self, owner_id: int, year: int, preview: list[tuple[str, int]]):
            super().__init__(timeout=600)
            self.owner_id = owner_id
            self.year = int(year)
            self.preview = preview  # [(uid, cash), ...]

        async def interaction_check(self, btn_inter: Interaction) -> bool:
            # Owner-only clicks
            if btn_inter.user.id != self.owner_id:
                await btn_inter.response.send_message("Owner only.", ephemeral=True)
                return False
            return True

        @button(label="Confirm Cut (3 players)", style=ButtonStyle.danger)
        async def confirm(self, btn: Button, btn_inter: Interaction):
            await btn_inter.response.defer()

            # Sanity checks again at commit time
            if not await _mode_is("elimination"):
                await btn_inter.followup.send("⛔ Not in elimination mode anymore. Aborting.")
                self.disable_all_items()
                return

            ry2 = await _current_result_year()
            if ry2 != self.year:
                await btn_inter.followup.send(f"⛔ Result year changed (now DB {ry2}). Aborting.")
                self.disable_all_items()
                return

            already2 = await db_client.market.portfolios.count_documents({"elim_year": int(self.year)})
            if already2 > 0:
                await btn_inter.followup.send(f"⛔ Eliminations for DB {self.year} already executed.")
                self.disable_all_items()
                return

            # Recompute bottom 3 at commit to avoid race; we expect they match preview.
            current = await _bottom_three_survivors()
            if len(current) < 3:
                await btn_inter.followup.send("❌ Not enough survivors now. Aborting.")
                self.disable_all_items()
                return

            # Mark eliminated with snapshot (order = 1..3 within the round)
            for idx, (uid, cash) in enumerate(current, start=1):
                await _set_eliminated(uid, self.year, cash=cash, order=idx)

            # Announce and freeze buttons
            lines_now = [f"- <@{uid}>" for uid, _ in current]
            done_embed = Embed(
                title=f"Elimination Executed — DB {self.year}",
                description=(
                    "The following players are **eliminated** (cannot buy/sell; admin override disabled):\n"
                    + "\n".join(lines_now) +
                    "\n\nEliminated players may still **transfer** hint points to support others (not self)."
                ),
                colour=BOT_COLOUR
            )
            self.disable_all_items()
            await btn_inter.followup.send(embed=done_embed)

        @button(label="Cancel", style=ButtonStyle.secondary)
        async def cancel(self, btn: Button, btn_inter: Interaction):
            await btn_inter.response.send_message("Elimination canceled.", ephemeral=True)
            self.disable_all_items()

        def disable_all_items(self):
            for child in self.children:
                child.disabled = True

    view = ElimCutView(OWNER_ID, ry, candidates)
    await interaction.followup.send(embed=embed, view=view)
    
# ===================== /finalize =====================
@bot.slash_command(
    name="finalize",
    description="OWNER: Declare the final winner (requires DB 11 = after the 10th result). Public."
)
@guard(require_private=False, public=True, owner_only=True)
async def finalize_winner(interaction: Interaction):
    if not interaction.response.is_done():
        await interaction.response.defer()

    # Must be after the 10th result → DB year == 11
    ry = await _current_result_year()
    if ry is None:
        await interaction.followup.send("❌ Cannot declare a winner: no result year recorded yet. Reveal & liquidate first.")
        return
    if int(ry) != 11:
        await interaction.followup.send(
            f"❌ The season is not finished yet. You can only declare the winner "
            f"**after the 10th result** (i.e., when **DB year = 11**). Current DB year: **{ry}**."
        )
        return

    pcol = db_client.market.portfolios
    candidates: list[tuple[str, int]] = []
    async for pf in pcol.find({}, {"cash": 1, "eliminated": 1}):
        if pf.get("eliminated"):
            continue  # eliminated players cannot win in elimination mode
        uid = str(pf["_id"])
        cash = int(pf.get("cash", 0))
        candidates.append((uid, cash))
    if not candidates:
        await interaction.followup.send("❌ No eligible portfolios to evaluate.")
        return

    candidates.sort(key=lambda x: x[1], reverse=True)
    top_cash = candidates[0][1]
    top_players = [(uid, cash) for uid, cash in candidates if cash == top_cash]

    # --- helper: get color info for a user (name + hex and an embed colour) ---
    async def _signup_color(uid: str) -> tuple[str, str, int]:
        """Return (color_name, color_hex, embed_colour_int). Falls back gracefully."""
        doc = await db_client.players.signups.find_one({"_id": uid}, {"color_name": 1, "color_hex": 1})
        name = (doc or {}).get("color_name") or "Unknown Color"
        hexv = (doc or {}).get("color_hex") or "#000000"
        try:
            col = _colour_from_hex(hexv)  # existing helper that returns nextcord Colour
            col_int = int(col.value)
        except Exception:
            col_int = int(BOT_COLOUR.value) if hasattr(BOT_COLOUR, "value") else 0x2F3136
        return name, hexv, col_int

    if len(top_players) == 1:
        winner_uid, winner_cash = top_players[0]
        color_name, color_hex, colour_int = await _signup_color(winner_uid)
        winner_mention = f"<@{winner_uid}>"
        embed = Embed(
            title="🏆 Final Winner Declared",
            description=(
                f"**Season complete (DB 11).**\n\n"
                f"**Winner**: {winner_mention}\n"
                f"**Color**: **{color_name}** `{color_hex}`\n"
                f"**Final Cash**: {winner_cash}"
            ),
            colour=colour_int,
        )
        await interaction.followup.send(embed=embed)
    else:
        lines = []
        for uid, _ in top_players:
            c_name, c_hex, _ = await _signup_color(uid)
            lines.append(f"- <@{uid}> — **{c_name}** `{c_hex}`")
        embed = Embed(
            title="🏆 Final Winners (Tie)",
            description=(
                f"**Season complete (DB 11).**\n\n"
                f"**Top Cash**: {top_cash}\n"
                "**Winners**:\n" + "\n".join(lines)
            ),
            colour=BOT_COLOUR,
        )
        await interaction.followup.send(embed=embed)


#===================================================
@bot.slash_command(
    name="help",
    description="Show help (loads readme_info.txt / readme_player.txt / readme_admin.txt)."
)
@guard(require_private=False, public=True)
async def cmd_help(
    interaction: Interaction,
    section: str = SlashOption(
        description="Which help to open.",
        required=False,
        choices={"Info": "info", "Player": "player", "Admin (owner only)": "admin"}
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
        missing = (HELP_FILE_ADMIN if requested == "admin"
                   else HELP_FILE_PLAYER if requested == "player"
                   else HELP_FILE_INFO)
        await interaction.followup.send(
            embed=Embed(title=title, description=f"❌ Missing file: `{missing}`", colour=BOT_COLOUR),
            view=view
        )
        return

    view.pages, view.title = pages, title
    msg = await interaction.followup.send(embed=view._cur_embed(), view=view)
    view.message = msg
#===================================================
@bot.message_command(name="Get Unix timestamp")
async def command_rac_time(interaction: Interaction, sent_message: Message):
    await interaction.response.defer()
    unix_timestamp = int(sent_message.created_at.timestamp())
    time_message = f"Message link: {sent_message.jump_url}\nUnix timestamp: {unix_timestamp}\n<t:{unix_timestamp}:f>" \
                   f"\n<t:{unix_timestamp}:R>"
    await interaction.followup.send(time_message)

bot.run(os.getenv("BOT_TOKEN"))
