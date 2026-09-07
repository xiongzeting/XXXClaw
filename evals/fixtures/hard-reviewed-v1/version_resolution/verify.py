from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'catalog': {'a': ['1.2.0', '1.10.0', '2.0.0'], 'b': ['0.0.1']}, 'requirements': [{'name': 'a', 'min': '1.0.0', 'max': '2.0.0'}]}),{'a': '1.10.0'})
print("PUBLIC_CHECK_OK")
