import json
from pathlib import Path
LEDGER=Path('ledger.json')
def charge(store, order_id, cents, fail_after_reserve=False):
    raise NotImplementedError
