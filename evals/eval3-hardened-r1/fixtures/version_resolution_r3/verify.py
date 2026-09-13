import copy,json
from solution import solve
x=json.load(open("sample.json",encoding="utf-8")); before=copy.deepcopy(x)
assert solve(x)=={'a': '2.0.0'}
assert x==before
print("SMOKE_OK")
