import copy,json
from solution import solve
x=json.load(open("sample.json",encoding="utf-8")); before=copy.deepcopy(x)
assert solve(x)==[{'query_id': 'q', 'days': [{'date': '2026-09-01', 'ids': ['e'], 'total': 2}]}]
assert x==before
print("SMOKE_OK")
