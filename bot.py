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
HEX_RE = re.compile(r'^#?(?:[0-9a-fA-F]{6})$')


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

# ============= "signup" Group of Commands =============
@bot.slash_command(
    name="signup",
    description="Player signup & roster tools",
    force_global=True
)
async def signup_cmd(interaction: Interaction):
    # group root; no direct execution
    pass

@signup_cmd.subcommand(
    name="join",
    description="Sign up and create your hint point inventory (0 pt).",
)
async def signup_join(
        interaction: Interaction,
        color_name: str = SlashOption(
            description="Your color name (e.g., Crimson, Ocean Blue)",
            required=True, max_length=32
        ),
        color_hex: str = SlashOption(
            description="Your color HEX (e.g., #FF0000 or FF0000)",
            required=True
        ),
):
    await interaction.response.defer()

    # capacity check
    players_db = db_client.players
    signups_col = players_db.signups
    current_count = await signups_col.count_documents({})
    if current_count >= MAX_PLAYERS:
        await interaction.followup.send(f"❌ Capacity full: {MAX_PLAYERS}/{MAX_PLAYERS}. Signups are closed.")
        return

    user_id = str(interaction.user.id)

    # one-time signup per user
    if await signups_col.find_one({"_id": user_id}) is not None:
        await interaction.followup.send("❌ You have already signed up. You can only sign up once.")
        return

    # must not already have a bank
    hp_db = db_client.hint_points
    balance_col = hp_db.balance
    if await balance_col.find_one({"_id": user_id}) is not None:
        await interaction.followup.send("❌ You already have a hint point inventory. Signup is only for new players.")
        return

    # hex validation
    norm_hex = _normalize_hex(color_hex)
    if norm_hex is None:
        await interaction.followup.send("❌ Invalid HEX code. Provide a 6-digit HEX like `#RRGGBB`.")
        return

    # record signup (user_id only on MongoDB, not seen)
    await signups_col.insert_one({
        "_id": user_id,
        "user_id": user_id,
        "user_name": interaction.user.name,
        "color_name": color_name.strip(),
        "color_hex": norm_hex,
        "signup_time": int(datetime.now().timestamp()),
    })

    # create 0pt bank with history
    await balance_col.insert_one({
        "_id": user_id,
        "balance": 0,
        "history": [{
            "time": int(datetime.now().timestamp()),
            "change": 0,
            "new_balance": 0,
            "user_id": str(bot.user.id),
            "reason": "Signup - inventory opened (0 pt)"
        }],
    })

    remaining = MAX_PLAYERS - (current_count + 1)
    embed = Embed(
        title="✅ Signup Complete",
        description=(
            f"**Player**: {interaction.user.mention}\n"
            f"**Color**: {color_name} `{norm_hex}`\n"
            f"**Slots left until closing**: **{remaining}** / {MAX_PLAYERS}"
        ),
        colour=_colour_from_hex(norm_hex)
    )
    await interaction.followup.send(embed=embed)

@signup_cmd.subcommand(
    name="view",
    description="View the roster: [Username] - [Color Name] - [HEX]",
)
async def signup_view(interaction: Interaction):
    # 실행 권한: OWNER만 (결과는 공개 메시지)
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

# ============= Subcommand "reset" of "hint_points"
@hint_points_cmd.subcommand(
    description="Resets all hint point and stock change info. Be careful with this command!",
)
async def reset(
        interaction: Interaction,
        confirm: str = SlashOption(
            description="put CONFIRM in this to proceed.",
            required=True,
        ),
):
    await interaction.response.defer()
    if not interaction.user.id == OWNER_ID:
        await interaction.followup.send("You are not Lunarisk. You cannot reset hint points. Go away.")
        return
    if not confirm == "CONFIRM":
        await interaction.followup.send("Command rejected. Put ``CONFIRM`` in the ``confirm`` option to reset all hint point and stock change info.")
        return
    db = db_client.hint_points
    collection = db.balance
    await collection.delete_many({})
    db = db_client.stocks
    collection = db.changes
    await collection.delete_many({})
    await interaction.followup.send("Successfully reset hint point and stock change info. Hopefully you didn't do this on accident!")

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
