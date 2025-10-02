from datetime import datetime
def now_ts() -> int:
    return int(datetime.now().timestamp())
