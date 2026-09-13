from datetime import datetime
def local_day(text,zone): return datetime.fromisoformat(text.replace("Z","+00:00")).date().isoformat()
