import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands

load_dotenv()
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

def _int(v: str | None):
    try:
        return int(v) if v and v.strip() else None
    except Exception:
        return None

ALLOWED_SIGNUP_CHANNEL_ID = _int(os.getenv("ALLOWED_SIGNUP_CHANNEL_ID"))
ALLOWED_GAME_CATEGORY_ID  = _int(os.getenv("ALLOWED_GAME_CATEGORY_ID"))

BOT_COLOUR_RGB = (169, 46, 33)

# Where your cogs live
BOT_EXTENSIONS = [
    "cramesia_SS.game.mode_main.ac_signup",
    "cramesia_SS.game.mode_main.ac_hint_points",
    "cramesia_SS.game.mode_main.ac_market",
    "cramesia_SS.game.mode_main.ac_stocks",
    "cramesia_SS.game.mode_main.ac_use_hint",
    "cramesia_SS.game.mode_main.ac_fun",
]

def create_bot() -> commands.Bot:
    intents = nextcord.Intents(guilds=True, members=True, messages=True, message_content=True)
    return commands.Bot(intents=intents)
