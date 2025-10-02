import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
load_dotenv()

_client = AsyncIOMotorClient(os.getenv("DB_URL"))
db = _client  # keep name parity with your old code

# handy shortcuts (same shape as before)
players = db.players
hint_points = db.hint_points
market = db.market
stocks = db.stocks
