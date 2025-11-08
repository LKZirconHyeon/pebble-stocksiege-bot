# cramesia_SS/constants.py
import re
from nextcord import Colour
from pathlib import Path

# --- odds tables ---
ODDS = {
    -80: 20, -75: 18, -70: 15, -60: 12, -50: 10, -45: 9, -40: 8, -35: 7,
    -30: 6, -25: 5, -20: 4, -15: 3, -10: 2, -5: 1, 0: 0, 5: -1, 10: -1,
    15: -2, 20: -2, 25: -3, 30: -3, 40: -4, 50: -5, 60: -6, 70: -7, 80: -8,
    90: -9, 100: -10, 150: -12, 200: -15, 300: -18, 400: -20,
}
ODDS_APOC = {
    0: -20, -5: -15, -10: -12, -15: -9, -20: -6, -25: -3, -30: 0,
    -40: 3, -50: 6, -60: 9, -70: 12, -75: 15, -80: 20,
}

# Detailed Odds
HIGH_INC  = [100, 150, 200, 300, 400]
MED_INC   = [40, 50, 60, 70, 80, 90]
LOW_INC   = [5, 10, 15, 20, 25, 30]

LOW_DEC   = [-5, -10, -15]
MED_DEC   = [-20, -25, -30, -35, -40, -45]
HIGH_DEC  = [-50, -60, -70, -75, -80]

# Detailed Strength
UP_TABLE = {
    "LOW":  {v: 1 for v in LOW_INC},
    "MED":  {v: 1 for v in MED_INC},
    "HIGH": {v: 1 for v in HIGH_INC},
}
DOWN_TABLE = {
    "LOW":  {v: 1 for v in LOW_DEC},
    "MED":  {v: 1 for v in MED_DEC},
    "HIGH": {v: 1 for v in HIGH_DEC},
}
ZERO_VALUES = [0]

# === ETU 정책 ===
ETU_ODDS_NEUTRAL_MIN = 41   # 41~59 제외
ETU_ODDS_NEUTRAL_MAX = 59
ETU_RATIOS = (3, 2, 1)      # LOW:MED:HIGH 비율 (해밀턴 배분)


# --- item setup ---
ITEM_CODES = list("ABCDEFGH")
NORMAL_STOCK_CHANGES = tuple(ODDS.keys())

# --- gameplay limits ---
MAX_PLAYERS = 24
STARTING_CASH = 500_000
APOC_START_CASH = 1_000_000_000
MAX_ITEM_UNITS = 9_999_999

PKG_ROOT = Path(__file__).resolve().parent
HELP_PAGE_LIMIT = 4000

# --- regex helpers ---
COLOR_NAME_RE = re.compile(r'^[A-Za-z ]{3,20}$')
HEX_RE = re.compile(r'^#?(?:[0-9a-fA-F]{6})$')

# --- colour helpers ---
def bot_colour() -> Colour:
    # customize this to your theme colour
    return Colour.from_rgb(169, 46, 33)

BOT_COLOUR = bot_colour()  # legacy alias, used in some files

__all__ = [
    "ODDS", "ODDS_APOC",
    "ITEM_CODES", "NORMAL_STOCK_CHANGES",
    "MAX_PLAYERS", "STARTING_CASH", "APOC_START_CASH", "MAX_ITEM_UNITS",
    "COLOR_NAME_RE", "HEX_RE",
    "bot_colour", "BOT_COLOUR",
    "HELP_FILE_INFO", "HELP_FILE_PLAYER", "HELP_FILE_ADMIN", "HELP_PAGE_LIMIT",
]
