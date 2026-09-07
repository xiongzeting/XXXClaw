from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'nodes': {'api': ['db', 'cache'], 'worker': ['db'], 'db': [], 'cache': ['db']}, 'requested': ['api', 'worker'], 'batch_size': 2}),{'batches': [['db'], ['cache', 'worker'], ['api']], 'order': ['db', 'cache', 'worker', 'api']})
print("PUBLIC_CHECK_OK")
