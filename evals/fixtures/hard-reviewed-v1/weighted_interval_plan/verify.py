from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'jobs': [{'id': 'long', 'start': 0, 'end': 10, 'value': 9}, {'id': 'a', 'start': 0, 'end': 5, 'value': 5}, {'id': 'b', 'start': 5, 'end': 10, 'value': 5}]}),{'value': 10, 'ids': ['a', 'b']})
print("PUBLIC_CHECK_OK")
