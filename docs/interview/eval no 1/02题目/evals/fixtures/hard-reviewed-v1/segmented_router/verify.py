from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'rules': [{'id': 'root', 'tenant': '*', 'prefix': '/', 'priority': 100, 'enabled': True}, {'id': 'api', 'tenant': '*', 'prefix': '/api', 'priority': 1, 'enabled': True}, {'id': 'z', 'tenant': 'a', 'prefix': '/api', 'priority': 2, 'enabled': True}, {'id': 'a', 'tenant': '*', 'prefix': '/api', 'priority': 2, 'enabled': True}], 'requests': [{'tenant': 'a', 'path': '/api/x'}, {'tenant': 'b', 'path': '/apix'}, {'tenant': 'b', 'path': '/api'}]}),['a', 'root', 'a'])
print("PUBLIC_CHECK_OK")
