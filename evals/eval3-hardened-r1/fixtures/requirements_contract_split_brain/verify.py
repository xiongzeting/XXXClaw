import copy,json
from solution import solve
x=json.load(open("sample.json",encoding="utf-8")); before=copy.deepcopy(x)
assert solve(x)=={'status': 404, 'body': {'ok': False, 'error': 1}}
assert x==before
print("SMOKE_OK")
