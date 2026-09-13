from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'gap': 5, 'events': [{'id': 'c', 'user': 'u', 't': 11, 'value': -2}, {'id': 'b', 'user': 'u', 't': 5, 'value': 3}, {'id': 'a', 'user': 'u', 't': 0, 'value': 2}, {'id': 'd', 'user': 'v', 't': 0, 'value': 0}]}),[{'user': 'u', 'start': 0, 'end': 5, 'first_id': 'a', 'event_ids': ['a', 'b'], 'total': 5}, {'user': 'u', 'start': 11, 'end': 11, 'first_id': 'c', 'event_ids': ['c'], 'total': -2}, {'user': 'v', 'start': 0, 'end': 0, 'first_id': 'd', 'event_ids': ['d'], 'total': 0}])
print("PUBLIC_CHECK_OK")
