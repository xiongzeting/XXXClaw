from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'document': {'a/b': {'~x': [1, 2]}, '01': 'old'}, 'operations': [{'op': 'set', 'path': '/a~1b/~0x/1', 'value': 3}, {'op': 'set', 'path': '/01', 'value': 'new'}, {'op': 'remove', 'path': '/a~1b/~0x/0'}]}),{'a/b': {'~x': [3]}, '01': 'new'})
print("PUBLIC_CHECK_OK")
