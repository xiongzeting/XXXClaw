from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'amount': 101, 'participants': [{'id': 'a', 'weight': 1}, {'id': 'b', 'weight': 2}]}),{'a': 34, 'b': 67})
print("PUBLIC_CHECK_OK")
