import os
import warnings
from dotenv import load_dotenv

from cramesia_SS.config import create_bot, BOT_EXTENSIONS

def main():
    load_dotenv()  # read .env
    token = os.getenv("BOT_TOKEN") or os.getenv("DISCORD_TOKEN") or ""
    if not token:
        raise RuntimeError("BOT_TOKEN (or DISCORD_TOKEN) missing in environment")

    bot = create_bot()
    for ext in BOT_EXTENSIONS:
        try:
            bot.load_extension(ext)
            print(f"[extensions] loaded {ext}")
        except Exception as e:
            import traceback
            print(f"[extensions] FAILED to load {ext}: {e}")
            traceback.print_exc()
    bot.run(token)

if __name__ == "__main__":
    main()
