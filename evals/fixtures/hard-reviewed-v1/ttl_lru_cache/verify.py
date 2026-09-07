from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'capacity': 2, 'ops': [{'t': 0, 'op': 'put', 'key': 'a', 'value': 0, 'ttl': 10}, {'t': 0, 'op': 'put', 'key': 'b', 'value': False, 'ttl': 10}, {'t': 1, 'op': 'get', 'key': 'a'}, {'t': 2, 'op': 'put', 'key': 'c', 'value': '', 'ttl': 10}, {'t': 3, 'op': 'get', 'key': 'b'}, {'t': 10, 'op': 'get', 'key': 'a'}]}),{'reads': [0, None, None], 'keys': ['c']})
print("PUBLIC_CHECK_OK")
