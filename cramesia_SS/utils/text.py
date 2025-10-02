from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP

def md_escape(s: str | None) -> str:
    if s is None:
        return ""
    return (str(s).replace("\\","\\\\").replace("_","\\_").replace("*","\\*")
            .replace("`","\\`").replace("~","\\~").replace("|","\\|").replace(">","\\>"))

def read_text(path: str) -> str | None:
    p = Path(path)
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None

def chunk_text(s: str, limit: int) -> list[str]:
    if not s:
        return [""]
    chunks, cur, n = [], [], 0
    for line in s.splitlines(keepends=True):
        if n + len(line) > limit:
            if cur:
                chunks.append("".join(cur)); cur, n = [], 0
            while len(line) > limit:
                chunks.append(line[:limit]); line = line[limit:]
        cur.append(line); n += len(line)
    if cur:
        chunks.append("".join(cur))
    return chunks or [""]

# ---- make these PUBLIC (no leading underscore) ----
def round_half_up_int(x: float | int) -> int:
    """0–4 down, 5–9 up (classic half-up)."""
    return int(Decimal(x).quantize(0, rounding=ROUND_HALF_UP))

def fmt_price(n: int | float) -> str:
    """Half-up round then thousands separators for display."""
    return f"{round_half_up_int(n):,}"

__all__ = [
    "md_escape", "read_text", "chunk_text",
    "round_half_up_int", "fmt_price",
]
