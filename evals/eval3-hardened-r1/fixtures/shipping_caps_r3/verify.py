import copy,json
from solution import solve
x=json.load(open("sample.json",encoding="utf-8")); before=copy.deepcopy(x)
assert solve(x)==[{'id': 'a', 'shipping_cents': 700}]
assert x==before
print("SMOKE_OK")
