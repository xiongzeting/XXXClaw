import copy,json
from solution import solve
x=json.load(open("sample.json",encoding="utf-8")); before=copy.deepcopy(x)
assert solve(x)=={'version': 1, 'users': [{'tenant': 'a', 'user': 'u', 'count': 1, 'amount': 5}], 'total_events': 1}
assert x==before
print("SMOKE_OK")
