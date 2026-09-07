from src.ui import connect
assert connect('db', timeout=7)=={'host':'db','timeout_ms':7}
