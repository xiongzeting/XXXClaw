from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'schema': 1, 'users': [{'name': 'A', 'email': None, 'meta': {'name': 'N'}}, {'profile': {'email': 'b@x'}}]}),{'schema': 2, 'users': [{'display_name': 'A', 'contact': 'unknown', 'meta': {'display_name': 'N'}}, {'profile': {'contact': 'b@x'}}]})
print("PUBLIC_CHECK_OK")
