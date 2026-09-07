from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'records': [{'id': 'b', 'score': 10, 'visible': True, 'extra': 0}, {'id': 'a', 'score': 10, 'visible': True}, {'id': 'c', 'score': 9, 'visible': True}], 'limit': 1}),{'items': [{'id': 'a', 'score': 10, 'visible': True}], 'next': {'score': 10, 'id': 'a'}, 'has_more': True})
print("PUBLIC_CHECK_OK")
