from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'initial': {'a': 0}, 'events': [{'seq': 2, 'event_id': 'b', 'op': 'set', 'key': 'a', 'value': 2}, {'seq': 1, 'event_id': 'a', 'op': 'set', 'key': 'a', 'value': 1}]}),{'state': {'a': 2}, 'last_seq': 2, 'event_ids': ['a', 'b']})
print("PUBLIC_CHECK_OK")
