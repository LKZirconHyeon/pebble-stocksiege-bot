from nextcord import Colour
from cramesia_SS.constants import HEX_RE

def normalize_hex(s: str | None) -> str | None:
    if not isinstance(s, str): return None
    s = s.strip()
    if not HEX_RE.fullmatch(s): return None
    core = s[1:] if s.startswith("#") else s
    return f"#{core.upper()}"

def colour_from_hex(hex_str: str) -> Colour:
    r = int(hex_str[1:3], 16); g = int(hex_str[3:5], 16); b = int(hex_str[5:7], 16)
    return Colour.from_rgb(r, g, b)
