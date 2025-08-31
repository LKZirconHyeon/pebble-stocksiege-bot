from motor.motor_asyncio import AsyncIOMotorClient  # MongoDB library
import nextcord  # Discord bot library
from nextcord.ext import commands
from nextcord import Interaction, SlashOption, ButtonStyle, Embed, Member, AllowedMentions, Colour, Message
from nextcord.ui import View, Button
from datetime import datetime
from dotenv import load_dotenv
import os
import re

load_dotenv()
db_client = AsyncIOMotorClient(os.getenv("DB_URL"))  # put link to database, MongoDB has free cloud service

bot = commands.Bot(intents=nextcord.Intents(guilds=True, members=True, message_content=True, messages=True))

OWNER_ID = int(os.getenv("OWNER_ID"))
ODDS = {-80: 20, -75: 18, -70: 15, -60: 12, -50: 10, -45: 9, -40: 8, -35: 7, -30: 6, -25: 5, -20: 4, -15: 3, -10: 2, -5: 1, 0: 0, 5: -1, 10: -1, 15: -2, 20: -2, 25: -3, 30: -3, 40: -4, 50: -5, 60: -6, 70: -7, 80: -8, 90: -9, 100: -10, 150: -12, 200: -15, 300: -18, 400: -20}
NORMAL_STOCK_CHANGES = tuple(ODDS.keys())
BOT_COLOUR = Colour.from_rgb(169, 46, 33)
MAX_PLAYERS = 24
ALLOWED_SIGNUP_CHANNEL_ID = 1309822981576593408  # /signup join Allowed Channel

COLOR_NAME_RE = re.compile(r'^[A-Za-z ]{3,20}$')
HEX_RE = re.compile(r'^#?(?:[0-9a-fA-F]{6})$')

STARTING_CASH = 500_000
MAX_ITEM_UNITS = 9_999_999
ITEM_CODES = list("ABCDEFGH")

# Base Format Layers
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

def _resolve_item_code(cfg_items: dict, ident: str) -> str | None:
    """
    ident: 'A'~'H' or configured name (case-insensitive).
    returns canonical item code 'A'~'H' or None.
    """
    if not ident:
        return None
    ident = ident.strip()
    up = ident.upper()
    if up in ITEM_CODES:
        return up
    # match by name
    for code in ITEM_CODES:
        nm = cfg_items.get(code, {}).get("name", "")
        if nm.lower() == ident.lower():
            return code
    return None

def _parse_orders(orders_raw: str) -> list[tuple[str, int]]:
    """
    Parse pairs from a free-form string.
    Accept separators: comma, pipe, semicolon, newline.
    Each pair is like: 'A 10' / 'Apple 5'
    """
    parts = re.split(r'[,\|\n;]+', orders_raw.strip())
    parsed: list[tuple[str,int]] = []
    for part in parts:
        t = part.strip()
        if not t:
            continue
        # e.g. "A 10" / "Ocean Blue 3"
        m = re.match(r'(.+?)\s+(\d+)$', t)
        if not m:
            # allow "A:10" or "A=10"
            m = re.match(r'(.+?)\s*[:=]\s*(\d+)$', t)
        if not m:
            raise ValueError(f"Cannot parse pair: '{t}' (use 'ItemName 10' or 'A 10')")
        name = m.group(1).strip()
        qty = int(m.group(2))
        if qty < 1 or qty > MAX_ITEM_UNITS:
            raise ValueError(f"Quantity out of range for '{t}' (1~{MAX_ITEM_UNITS})")
        parsed.append((name, qty))
    if not parsed:
        raise ValueError("No valid (item, quantity) pairs found.")
    return parsed
# Base Format Layers





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

    await interaction.response.defer()

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

    # 8) Create portfolio with starting cash and empty holdings
    await portfolios.insert_one({
        "_id": user_id,
        "user_id": user_id,
        "cash": STARTING_CASH,
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
    async for p in signups_col.find({}, projection={"user_name": 1, "color_name": 1, "color_hex": 1}).sort("signup_time", 1):
        lines.append(f"{p.get('user_name','(unknown)')} - {p.get('color_name','?')} - {p.get('color_hex','?')}")

    roster = "\n".join(lines) if lines else "_No signups yet_"
    embed = Embed(
        title=f"Signup Roster ({len(lines)}/{MAX_PLAYERS})",
        description=roster,
        colour=BOT_COLOUR
    )
    await interaction.followup.send(embed=embed)

# ========= signup reset (interactive confirm) =========
from nextcord.ui import View, Button
from nextcord import ButtonStyle

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
):
    # 권한
    if interaction.user.id != OWNER_ID:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("❌ Only the owner can run this command.")
        return

    await interaction.response.defer(ephemeral=True)

    # 0) 1차 안전장치
    if confirm != "CONFIRM":
        await interaction.followup.send("❌ Type `CONFIRM` to prepare reset preview.")
        return

    # 1) 입력 파싱/검증
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

    # 2) 미리보기 데이터 구성
    new_items = {code: {"name": name_list[i], "price": price_vals[i]} for i, code in enumerate(ITEM_CODES)}
    preview_lines = [f"{code}: **{new_items[code]['name']}** — {new_items[code]['price']}" for code in ITEM_CODES]
    preview = "\n".join(preview_lines)

    # 3) 확인용 View 정의
    class ResetConfirmView(View):
        def __init__(self, owner_id: int, items: dict):
            super().__init__(timeout=600)
            self.owner_id = owner_id
            self.items = items
            self.message = None

        async def interaction_check(self, btn_inter: Interaction) -> bool:
            # 오너만 버튼 클릭 가능
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

    # Apply 버튼
    class ApplyButton(Button):
        def __init__(self):
            super().__init__(style=ButtonStyle.danger, label="Apply & Wipe", emoji="🗑️")

        async def callback(self, btn_inter: Interaction):
            # 실제 삭제 + 신규 설정 저장
            try:
                # 1) 참여자/힌트/포트폴리오 삭제
                sres = await db_client.players.signups.delete_many({})
                bres = await db_client.hint_points.balance.delete_many({})
                pres = await db_client.market.portfolios.delete_many({})

                # 2) 품목 설정 교체
                cfg_col = db_client.market.config
                current = await cfg_col.find_one({"_id": "current"})
                if current:
                    await cfg_col.delete_one({"_id": "current"})
                await cfg_col.insert_one({"_id": "current", "items": self.view.items, "updated_at": _now_ts()})

                # 버튼 비활성 + 완료 메시지
                await btn_inter.response.edit_message(
                    embed=Embed(
                        title="✅ Reset Applied",
                        description=(
                            "**Wiped collections**\n"
                            f"- Deleted signups: {sres.deleted_count}\n"
                            f"- Deleted hint banks: {bres.deleted_count}\n"
                            f"- Deleted portfolios: {pres.deleted_count}\n\n"
                            "**New Items (A~H)**\n" + 
                            "\n".join([f"{c}: **{self.view.items[c]['name']}** — {self.view.items[c]['price']}" for c in ITEM_CODES])
                        ),
                        colour=BOT_COLOUR
                    ),
                    view=None
                )
            except Exception as e:
                await btn_inter.response.edit_message(
                    content=f"❌ Error during reset: {e}",
                    view=None
                )

    # Cancel 버튼
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

    view = ResetConfirmView(OWNER_ID, new_items)
    view.add_item(ApplyButton())
    view.add_item(CancelButton())

    embed = Embed(
        title="⚠️ Reset Preview",
        description=(
            "If you press **Apply & Wipe**, the bot will:\n"
            "1) Delete **signups**, **hint point inventories**, and **portfolios**.\n"
            "2) Replace **market items** with the following A~H config.\n\n"
            "**New Items (A~H) Preview**\n" + preview
        ),
        colour=BOT_COLOUR
    )

    msg = await interaction.followup.send(embed=embed, view=view)
    view.message = msg





# ============= General "hint_points" Group of Commands
@bot.slash_command(
    name="hint_points",
    description="Manage your hint points",
    force_global=True)
async def hint_points_cmd(interaction: Interaction):
    pass

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
        await interaction.followup.send(f"{user.mention} has not set up their hint points yet.\n\n"
                                        f"To set up hint points, use the {bank_setup.get_mention(guild=None)} command.")
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
        await interaction.followup.send(f"{user.mention} has not set up their hint points yet.\n\n"
                                        f"To set up hint points, use the {bank_setup.get_mention(guild=None)} command.")
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
        await interaction.followup.send(f"{user.mention} has not set up their hint points yet.\n\n"
                                        f"To set up hint points, use the {bank_setup.get_mention(guild=None)} command.")
        return
    existing_bank["history"].sort(key=lambda x: x["time"], reverse=True)
    history = paginate_list(existing_bank["history"])
    balance_view = BankBalanceViewer(0, existing_bank["balance"], history, user)
    balance_embed = format_balance_embed(balance_view)
    msg = await interaction.followup.send(
        embed=balance_embed,
        view=balance_view
    )

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
        await interaction.followup.send(f"You have not set up your hint points yet.\n\n"
                                        f"To set up hint points, use the {bank_setup.get_mention(guild=None)} command.")
        return
    if receive_bank is None:
        await interaction.followup.send(f"{user.mention} has not set up their hint points yet.\n\n"
                                        f"To set up hint points, use the {bank_setup.get_mention(guild=None)} command.")
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





# ============= "market" Group of Commands =============
@bot.slash_command(
    name="market",
    description="Market info & trading (view, purchase, sell, portfolio)",
    force_global=True
)
async def market_cmd(interaction: Interaction):
    # group root; no direct execution
    pass

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

# ---------- Public: see configurable items ----------
@market_cmd.subcommand(
    name="view",
    description="View current stock items (A–H) and prices."
)
async def market_view(interaction: Interaction):
    """Public: show item codes A–H with their configured names & prices."""
    await interaction.response.defer()  # public reply

    cfg = await _get_market_config()
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured yet.")
        return

    items = cfg["items"]
    lines = []
    for code in ITEM_CODES:
        info = items.get(code)
        if not info:
            lines.append(f"{code}: _(not set)_")
            continue
        nm = info.get("name", "(unnamed)")
        pr = info.get("price", "?")
        lines.append(f"{code}: **{nm}** — {pr}")

    await interaction.followup.send(
        embed=Embed(
            title="Market Items (A–H)",
            description="\n".join(lines),
            colour=BOT_COLOUR
        )
    )

# ---------- Personal portfolio (ephemeral) ----------
@market_cmd.subcommand(
    name="portfolio",
    description="View your portfolio: Unspent Cash, Total Cash, and your holdings."
)
async def market_portfolio(interaction: Interaction):
    """Ephemeral: user sees their own cash, total, and non-zero holdings."""
    await interaction.response.defer(ephemeral=True)

    cfg = await _get_market_config()
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured yet.")
        return
    items = cfg["items"]

    uid = str(interaction.user.id)
    pf = await db_client.market.portfolios.find_one({"_id": uid})
    if not pf:
        await interaction.followup.send("❌ No portfolio. Use **/signup join** first.")
        return

    holdings_lines = []
    for code in ITEM_CODES:
        qty = int(pf["holdings"].get(code, 0))
        if qty > 0:
            value = qty * int(items[code]["price"])
            holdings_lines.append(f"{code} - {items[code]['name']}: {qty} (≈ {value})")

    unspent, total = _portfolio_totals(items, pf)
    desc = (
        f"**Unspent Cash**: {unspent}\n"
        f"**Total Cash**: {total}\n\n" +
        ("**Holdings**\n" + "\n".join(holdings_lines) if holdings_lines else "_No holdings_")
    )
    await interaction.followup.send(embed=Embed(
        title=f"Portfolio — {interaction.user.display_name}",
        description=desc,
        colour=BOT_COLOUR
    ))

# ---------- Owner-only: inspect someone else’s portfolio ----------
@market_cmd.subcommand(
    name="admin_view",
    description="OWNER: View someone else's portfolio."
)
async def market_admin_view(
    interaction: Interaction,
    user: Member = SlashOption(description="User to view", required=True)
):
    if interaction.user.id != OWNER_ID:
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("❌ Only the owner can run this command.")
        return

    await interaction.response.defer(ephemeral=True)

    cfg = await _get_market_config()
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured yet.")
        return
    items = cfg["items"]

    uid = str(user.id)
    pf = await db_client.market.portfolios.find_one({"_id": uid})
    if not pf:
        await interaction.followup.send("❌ No portfolio for that user.")
        return

    holdings_lines = []
    for code in ITEM_CODES:
        qty = int(pf["holdings"].get(code, 0))
        if qty > 0:
            value = qty * int(items[code]["price"])
            holdings_lines.append(f"{code} - {items[code]['name']}: {qty} (≈ {value})")

    unspent, total = _portfolio_totals(items, pf)
    desc = (
        f"**Unspent Cash**: {unspent}\n"
        f"**Total Cash**: {total}\n\n" +
        ("**Holdings**\n" + "\n".join(holdings_lines) if holdings_lines else "_No holdings_")
    )
    await interaction.followup.send(embed=Embed(
        title=f"Portfolio — {user.display_name}",
        description=desc,
        colour=BOT_COLOUR
    ))

# ---------- Purchase: spend Unspent Cash to increase holdings ----------
@market_cmd.subcommand(
    name="purchase",
    description="Buy items. Multiple pairs allowed (e.g., 'A 10, Apple 3')."
)
async def market_purchase(
    interaction: Interaction,
    orders: str = SlashOption(
        description="Pairs like 'A 10, C 5' or names 'Apple 3' (comma/|/;/newline separated).",
        required=True
    ),
):
    """Ephemeral: parse pairs; ensure no debt; enforce per-item cap; update portfolio."""
    await interaction.response.defer(ephemeral=True)

    cfg = await _get_market_config()
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured yet.")
        return
    items = cfg["items"]

    uid = str(interaction.user.id)
    pf_col = db_client.market.portfolios
    pf = await pf_col.find_one({"_id": uid})
    if not pf:
        await interaction.followup.send("❌ No portfolio. Use **/signup join** first.")
        return

    # Parse "(item, qty)" pairs
    try:
        pairs = _parse_orders(orders)
    except ValueError as e:
        await interaction.followup.send(f"❌ {e}")
        return

    # Pre-calc total cost and per-item limit
    add_map: dict[str, int] = {}
    total_cost = 0
    for ident, qty in pairs:
        code = _resolve_item_code(items, ident)
        if not code:
            await interaction.followup.send(f"❌ Unknown item: `{ident}`")
            return
        price = int(items[code]["price"])
        cur_qty = int(pf["holdings"].get(code, 0))
        if cur_qty + qty > MAX_ITEM_UNITS:
            await interaction.followup.send(f"❌ Holding limit exceeded for {code}. Max {MAX_ITEM_UNITS}.")
            return
        add_map[code] = add_map.get(code, 0) + qty
        total_cost += price * qty

    # Debt check: Unspent Cash must stay >= 0 after purchase
    if pf["cash"] - total_cost < 0:
        await interaction.followup.send(
            f"❌ Not enough cash. Need {total_cost}, you have {pf['cash']} (debt not allowed)."
        )
        return

    # Apply updates
    for code, qty in add_map.items():
        pf["holdings"][code] = int(pf["holdings"].get(code, 0)) + qty
    pf["cash"] -= total_cost
    await pf_col.update_one(
        {"_id": uid},
        {"$set": {"holdings": pf["holdings"], "cash": pf["cash"], "updated_at": _now_ts()}}
    )

    # Reply summary
    lines = [f"{code} ({items[code]['name']}): +{qty} @ {items[code]['price']}"
             for code, qty in add_map.items()]
    unspent, total = _portfolio_totals(items, pf)
    await interaction.followup.send(
        "✅ Purchase complete:\n- " + "\n- ".join(lines) +
        f"\n**Unspent Cash**: {unspent}\n**Total Cash**: {total}"
    )

# ---------- Sell: convert holdings back to Unspent Cash ----------
@market_cmd.subcommand(
    name="sell",
    description="Sell items. Multiple pairs allowed; partial sell allowed."
)
async def market_sell(
    interaction: Interaction,
    orders: str = SlashOption(
        description="Pairs like 'A 10, C 5' or names 'Apple 3' (comma/|/;/newline separated).",
        required=True
    ),
):
    """Ephemeral: sell up to held quantity; refund goes into Unspent Cash."""
    await interaction.response.defer(ephemeral=True)

    cfg = await _get_market_config()
    if not cfg or "items" not in cfg:
        await interaction.followup.send("❌ Market is not configured yet.")
        return
    items = cfg["items"]

    uid = str(interaction.user.id)
    pf_col = db_client.market.portfolios
    pf = await pf_col.find_one({"_id": uid})
    if not pf:
        await interaction.followup.send("❌ No portfolio. Use **/signup join** first.")
        return

    # Parse "(item, qty)" pairs
    try:
        pairs = _parse_orders(orders)
    except ValueError as e:
        await interaction.followup.send(f"❌ {e}")
        return

    sold_lines = []
    total_gain = 0
    any_success = False

    for ident, req_qty in pairs:
        code = _resolve_item_code(items, ident)
        if not code:
            sold_lines.append(f"❌ Unknown item: `{ident}` — skipped")
            continue

        have = int(pf["holdings"].get(code, 0))
        if have <= 0:
            sold_lines.append(f"❌ {code} ({items[code]['name']}): you have 0 — rejected")
            continue

        # Partial sell allowed: sell as much as the user has
        sell_qty = min(have, req_qty)
        gain = sell_qty * int(items[code]["price"])

        pf["holdings"][code] = have - sell_qty
        pf["cash"] += gain
        total_gain += gain
        any_success = True

        if sell_qty < req_qty:
            sold_lines.append(
                f"⚠️ {code} ({items[code]['name']}): requested {req_qty}, sold {sell_qty} (all you had) @ {items[code]['price']}"
            )
        else:
            sold_lines.append(f"✅ {code} ({items[code]['name']}): -{sell_qty} @ {items[code]['price']}")

    if any_success:
        await pf_col.update_one(
            {"_id": uid},
            {"$set": {"holdings": pf["holdings"], "cash": pf["cash"], "updated_at": _now_ts()}}
        )

    unspent, total = _portfolio_totals(items, pf)
    summary = "\n- ".join(sold_lines) if sold_lines else "(nothing parsed)"
    await interaction.followup.send(
        f"{summary}\n\n**Unspent Cash**: {unspent} (+{total_gain})\n**Total Cash**: {total}"
    )





# ============= "stock" Group of Commands =============
@bot.slash_command(
    name="stock_change",
    description="Set stock changes, for giving out hints.",
    force_global=True)
async def stock_change_cmd(interaction: Interaction):
    pass

@stock_change_cmd.subcommand(
    name="set",
    description="Set stock changes, for giving out hints.",
)
async def stock_change_set(
        interaction: Interaction,
        changes: str = SlashOption(
            description="Put each change in here, separated by a space.",
            required=True,
        ),
        year: int = SlashOption(
            description="The year the changes occurred.",
            required=True,
        ),
):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.id == OWNER_ID:
        await interaction.followup.send("You are not Lunarisk. You cannot reset hint points. Go away.")
        return
    changes = changes.split()
    if not len(changes) == 8:
        await interaction.followup.send("This bot only works with 8 stocks. Contact bohaska to code stuff if you're working with more than that.")
        return
    if any(int(change) not in NORMAL_STOCK_CHANGES for change in changes):
        await interaction.followup.send("Some of the changes you input are invalid changes.")
        return
    db = db_client.stocks
    collection = db.changes
    capitalized_letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    new_change = {capitalized_letters[index]: int(changes[index]) for index in range(0, len(changes))}
    existing_change = await collection.find_one({"_id": year})
    msg = ""
    if existing_change is not None:
        await collection.delete_one({"_id": year})
        msg = "\nNote: The year you input was found in the database, so it was replaced with your new changes."
    new_change["_id"] = year
    await collection.insert_one(new_change)
    await interaction.followup.send(f"Successfully recorded stock changes for year {year}" + msg)

async def year_autocomplete(interaction: Interaction, year: str):
    db = db_client.stocks
    collection = db.changes
    years = []
    async for entry in collection.find({}, ["_id"]):
        years.append(entry)
    autocomplete_years = [row["_id"] for row in years]
    await interaction.response.send_autocomplete(autocomplete_years)
    return

@stock_change_cmd.subcommand(
    name="view",
    description="View stock changes you set for a year.",
)
async def stock_change_view(
        interaction: Interaction,
        year: int = SlashOption(
            description="The year you want to view.",
            required=True,
            autocomplete_callback=year_autocomplete
        ),
):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.id == OWNER_ID:
        await interaction.followup.send("You are not Lunarisk. You cannot view stock changes. Go away.")
        return
    db = db_client.stocks
    collection = db.changes
    existing_change = await collection.find_one({"_id": year})
    if existing_change is None:
        await interaction.followup.send("This year doesn't exist in the database.")
        return
    msg = ""
    for stock, change in existing_change.items():
        if stock == "_id":
            continue
        msg += f"{stock}: {'+' if change > 0 else ''}{change}%\n"
    await interaction.followup.send(f"# Year {year} changes\n\n" + msg)

@stock_change_cmd.subcommand(
    name="odds",
    description="View stock odds. Owner-only.",
)
async def stock_change_odds(
        interaction: Interaction,
):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.id == OWNER_ID:
        await interaction.followup.send("You are not Lunarisk. You cannot view stock odds. Go away.")
        return
    db = db_client.stocks
    collection = db.changes
    years = []
    async for year in collection.find({}):
        years.append(year)
    print(years)
    years.sort(key=lambda year: year['_id'])
    r_years = years[:-1]
    if not years:
        await interaction.followup.send("There is no stock info in this bot's database.\n"
                                        "Either you did not update stock info,"
                                        " or this is the start of a season (All stocks 50%).")
        return
    r_odds = calculate_odds(r_years)
    if r_years:
        r_odd_info = "R-hint (Does not include latest year changes)\n\n"
        for stock, odd in r_odds.items():
            r_odd_info += f"{stock}: {odd}%\n"
    else:
        r_odd_info = ""
    owner_odds = calculate_odds(years)
    owner_odds_info = "Owner-only odds (Includes latest year changes)\n\n"
    for stock, odd in owner_odds.items():
        owner_odds_info += f"{stock}: {odd}%\n"
    msg = r_odd_info + "\n\n" + owner_odds_info
    await interaction.followup.send(
        msg,
    )





# Hint Point System
@bot.slash_command(
    name="use_hint",
    description="Use your hint points",
    force_global=True)
async def use_hint(interaction: Interaction):
    pass

def calculate_odds(years: list):
    print(years)
    stocks = "ABCDEFGH"
    stock_odds = {stock: 50 for stock in stocks}
    years.sort(key=lambda year: year['_id'])
    for year in years:
        print(year)
        for stock, change in year.items():
            if stock == "_id":
                continue
            stock_odds[stock] += ODDS[change]
            stock_odds[stock] = max(min(stock_odds[stock], 100), 0)
    return stock_odds

@use_hint.subcommand(
    name="r",
    description="Reveal odds of all stocks. Costs 1 HP.",
)
async def r_hint(
        interaction: Interaction,
        confirm: str = SlashOption(
            description="put R HINT in this to proceed.",
            required=True,
        ),
):
    await interaction.response.defer()
    if not confirm == "R HINT":
        await interaction.followup.send("Command rejected. Put ``R HINT`` in the ``confirm`` option to use an r hint.")
        return
    db = db_client.hint_points
    hint_collection = db.balance
    hint_bank = await hint_collection.find_one({"_id": str(interaction.user.id)})
    if hint_bank is None:
        await interaction.followup.send("You don't have a hint point bank. Contact Lunarisk to get one.")
        return
    hint_points = hint_bank["balance"]
    if hint_points < 1:
        hint_bank["history"].sort(key=lambda x: x["time"], reverse=True)
        history = paginate_list(hint_bank["history"])
        balance_view = BankBalanceViewer(0, hint_bank["balance"], history, interaction.user)
        balance_embed = format_balance_embed(balance_view)
        await interaction.followup.send(
            f"You need 1 hint point to use an R hint. You only have {hint_points} hint points.",
            embed=balance_embed,
            view=balance_view,
        )
        return
    else:
        db = db_client.stocks
        collection = db.changes
        years = []
        async for year in collection.find({}):
            years.append(year)
        years.sort(key=lambda year: year['_id'])
        years = years[:-1]
        if not years:
            await interaction.followup.send("There is no stock info in this bot's database.\n"
                                            "Either Lunarisk did not update stock info,"
                                            " or this is the start of a season (All stocks 50%).")
            return
        odds = calculate_odds(years)
        hint_bank["balance"] -= 1
        history_entry = {
            "time": int(datetime.now().timestamp()),
            "change": -1,
            "new_balance": hint_bank["balance"],
            "user_id": str(bot.user.id),
            "reason": f"Used R-hint."
        }
        hint_bank["history"].append(history_entry)
        updates = {
            "balance": hint_bank["balance"],
            "history": hint_bank["history"],
        }
        await hint_collection.update_one({"_id": str(interaction.user.id)}, {"$set": updates})
        hint_bank["history"].sort(key=lambda x: x["time"], reverse=True)
        history = paginate_list(hint_bank["history"])
        balance_view = BankBalanceViewer(0, hint_bank["balance"], history, interaction.user)
        balance_embed = format_balance_embed(balance_view)
        odd_info = "Used R-hint!\n\n"
        for stock, odd in odds.items():
            odd_info += f"{stock}: {odd}%\n"
        await interaction.followup.send(
            odd_info,
            embed=balance_embed,
            view=balance_view,
        )
        return

@use_hint.subcommand(
    name="lvl1",
    description="Reveals the strength of change for a single stock. Costs 1 HP.",
)
async def lvl1_hint(
        interaction: Interaction,
        stock: str = SlashOption(
            description="The stock you want to use this hint on.",
            required=True,
            choices=[stock for stock in "ABCDEFGH"]
        ),
        confirm: str = SlashOption(
            description="put LVL1 in this to proceed.",
            required=True,
        ),
):
    await interaction.response.defer()
    if not confirm == "LVL1":
        await interaction.followup.send("Command rejected. Put ``LVL1`` in the ``confirm`` option to use a hint.")
        return
    db = db_client.hint_points
    hint_collection = db.balance
    hint_bank = await hint_collection.find_one({"_id": str(interaction.user.id)})
    if hint_bank is None:
        await interaction.followup.send("You don't have a hint point bank. Contact Lunarisk to get one.")
        return
    hint_points = hint_bank["balance"]
    if hint_points < 1:
        hint_bank["history"].sort(key=lambda x: x["time"], reverse=True)
        history = paginate_list(hint_bank["history"])
        balance_view = BankBalanceViewer(0, hint_bank["balance"], history, interaction.user)
        balance_embed = format_balance_embed(balance_view)
        await interaction.followup.send(
            f"You need 1 hint point to use a level 1 hint. You only have {hint_points} hint points.",
            embed=balance_embed,
            view=balance_view,
        )
        return
    else:
        db = db_client.stocks
        collection = db.changes
        years = []
        async for year in collection.find({}):
            years.append(year)
        years.sort(key=lambda year: year['_id'])
        try:
            year = years[-1]
        except IndexError:
            await interaction.followup.send("There is no stock info in this bot's database.\n"
                                            "Either Lunarisk did not update stock info,"
                                            " or this is the start of a season (All stocks 50%).")
            return
        stock_change = year[stock]
        odds_change = ODDS[stock_change]
        change_info = "Used level 1 hint!\n\n"
        if abs(odds_change) <= 3:
            change_info += f"Change of stock {stock}: **Low**"
        elif abs(odds_change) <= 9:
            change_info += f"Change of stock {stock}: **Medium**"
        else:
            change_info += f"Change of stock {stock}: **High**"
        hint_bank["balance"] -= 1
        history_entry = {
            "time": int(datetime.now().timestamp()),
            "change": -1,
            "new_balance": hint_bank["balance"],
            "user_id": str(bot.user.id),
            "reason": f"Used level 1 hint on stock {stock}."
        }
        hint_bank["history"].append(history_entry)
        updates = {
            "balance": hint_bank["balance"],
            "history": hint_bank["history"],
        }
        await hint_collection.update_one({"_id": str(interaction.user.id)}, {"$set": updates})
        hint_bank["history"].sort(key=lambda x: x["time"], reverse=True)
        history = paginate_list(hint_bank["history"])
        balance_view = BankBalanceViewer(0, hint_bank["balance"], history, interaction.user)
        balance_embed = format_balance_embed(balance_view)
        await interaction.followup.send(
            change_info,
            embed=balance_embed,
            view=balance_view,
        )
        return

@use_hint.subcommand(
    name="lvl2",
    description="Gives 2 possible changes for a stock. Costs 2 HP",
)
async def lvl2_hint(
        interaction: Interaction,
        stock: str = SlashOption(
            description="The stock you want to use this hint on.",
            required=True,
            choices=[stock for stock in "ABCDEFGH"]
        ),
        confirm: str = SlashOption(
            description="put LVL2 in this to proceed.",
            required=True,
        ),
):
    await interaction.response.defer()
    if not confirm == "LVL2":
        await interaction.followup.send("Command rejected. Put ``LVL2`` in the ``confirm`` option to use a hint.")
        return
    db = db_client.hint_points
    hint_collection = db.balance
    hint_bank = await hint_collection.find_one({"_id": str(interaction.user.id)})
    if hint_bank is None:
        await interaction.followup.send("You don't have a hint point bank. Contact Lunarisk to get one.")
        return
    hint_points = hint_bank["balance"]
    if hint_points < 2:
        hint_bank["history"].sort(key=lambda x: x["time"], reverse=True)
        history = paginate_list(hint_bank["history"])
        balance_view = BankBalanceViewer(0, hint_bank["balance"], history, interaction.user)
        balance_embed = format_balance_embed(balance_view)
        await interaction.followup.send(
            f"You need 2 hint point to use a level 2 hint. You only have {hint_points} hint points.",
            embed=balance_embed,
            view=balance_view,
        )
        return
    else:
        db = db_client.stocks
        collection = db.changes
        years = []
        async for year in collection.find({}):
            years.append(year)
        years.sort(key=lambda year: year['_id'])
        try:
            year = years[-1]
        except IndexError:
            await interaction.followup.send("There is no stock info in this bot's database.\n"
                                            "Either Lunarisk did not update stock info,"
                                            " or this is the start of a season (All stocks 50%).")
            return
        stock_change = year[stock]
        odds_change = ODDS[stock_change]
        change_info = "Used level 2 hint!\n\n"
        for change, other_odd_change in ODDS.items():
            if other_odd_change == -odds_change:
                opposite_change = change
                break
        if opposite_change > stock_change:
            change_info += f"Possible changes for stock {stock}: **{opposite_change}%, {stock_change}%**"
        else:
            change_info += f"Possible changes for stock {stock}: **{stock_change}%, {opposite_change}%**"
        hint_bank["balance"] -= 2
        history_entry = {
            "time": int(datetime.now().timestamp()),
            "change": -2,
            "new_balance": hint_bank["balance"],
            "user_id": str(bot.user.id),
            "reason": f"Used level 2 hint on stock {stock}."
        }
        hint_bank["history"].append(history_entry)
        updates = {
            "balance": hint_bank["balance"],
            "history": hint_bank["history"],
        }
        await hint_collection.update_one({"_id": str(interaction.user.id)}, {"$set": updates})
        hint_bank["history"].sort(key=lambda x: x["time"], reverse=True)
        history = paginate_list(hint_bank["history"])
        balance_view = BankBalanceViewer(0, hint_bank["balance"], history, interaction.user)
        balance_embed = format_balance_embed(balance_view)
        await interaction.followup.send(
            change_info,
            embed=balance_embed,
            view=balance_view,
        )
        return

@use_hint.subcommand(
    name="lvl3",
    description="Shows whether a stock will increase or decrease. Costs 3 HP",
)
async def lvl3_hint(
        interaction: Interaction,
        stock: str = SlashOption(
            description="The stock you want to use this hint on.",
            required=True,
            choices=[stock for stock in "ABCDEFGH"]
        ),
        confirm: str = SlashOption(
            description="put LVL3 in this to proceed.",
            required=True,
        ),
):
    await interaction.response.defer()
    if not confirm == "LVL3":
        await interaction.followup.send("Command rejected. Put ``LVL3`` in the ``confirm`` option to use a hint.")
        return
    db = db_client.hint_points
    hint_collection = db.balance
    hint_bank = await hint_collection.find_one({"_id": str(interaction.user.id)})
    if hint_bank is None:
        await interaction.followup.send("You don't have a hint point bank. Contact Lunarisk to get one.")
        return
    hint_points = hint_bank["balance"]
    if hint_points < 3:
        hint_bank["history"].sort(key=lambda x: x["time"], reverse=True)
        history = paginate_list(hint_bank["history"])
        balance_view = BankBalanceViewer(0, hint_bank["balance"], history, interaction.user)
        balance_embed = format_balance_embed(balance_view)
        await interaction.followup.send(
            f"You need 3 hint point to use a level 3 hint. You only have {hint_points} hint points.",
            embed=balance_embed,
            view=balance_view,
        )
        return
    else:
        db = db_client.stocks
        collection = db.changes
        years = []
        async for year in collection.find({}):
            years.append(year)
        years.sort(key=lambda year: year['_id'])
        try:
            year = years[-1]
        except IndexError:
            await interaction.followup.send("There is no stock info in this bot's database.\n"
                                            "Either Lunarisk did not update stock info,"
                                            " or this is the start of a season (All stocks 50%).")
            return
        stock_change = year[stock]
        change_info = "Used level 3 hint!\n\n"
        if stock_change > 0:
            change_info += f"Stock {stock} will **increase**"
        elif stock_change < 0:
            change_info += f"Stock {stock} will **decrease**"
        else:
            change_info += f"Stock {stock} will **not change in price**"
        hint_bank["balance"] -= 3
        history_entry = {
            "time": int(datetime.now().timestamp()),
            "change": -3,
            "new_balance": hint_bank["balance"],
            "user_id": str(bot.user.id),
            "reason": f"Used level 3 hint on stock {stock}."
        }
        hint_bank["history"].append(history_entry)
        updates = {
            "balance": hint_bank["balance"],
            "history": hint_bank["history"],
        }
        await hint_collection.update_one({"_id": str(interaction.user.id)}, {"$set": updates})
        hint_bank["history"].sort(key=lambda x: x["time"], reverse=True)
        history = paginate_list(hint_bank["history"])
        balance_view = BankBalanceViewer(0, hint_bank["balance"], history, interaction.user)
        balance_embed = format_balance_embed(balance_view)
        await interaction.followup.send(
            change_info,
            embed=balance_embed,
            view=balance_view,
        )
        return

@bot.message_command(name="Get Unix timestamp")
async def command_rac_time(interaction: Interaction, sent_message: Message):
    await interaction.response.defer()
    unix_timestamp = int(sent_message.created_at.timestamp())
    time_message = f"Message link: {sent_message.jump_url}\nUnix timestamp: {unix_timestamp}\n<t:{unix_timestamp}:f>" \
                   f"\n<t:{unix_timestamp}:R>"
    await interaction.followup.send(time_message)

bot.run(os.getenv("BOT_TOKEN"))
