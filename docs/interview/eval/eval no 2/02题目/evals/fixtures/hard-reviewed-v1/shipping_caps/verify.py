from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'packages': [{'id': 'b', 'weight_g': 1001, 'zone': 'remote', 'fragile': True}, {'id': 'a', 'weight_g': 0, 'zone': 'local', 'fragile': False}], 'cap_cents': 1000}),[{'id': 'a', 'shipping_cents': 500}, {'id': 'b', 'shipping_cents': 500}])
print("PUBLIC_CHECK_OK")
