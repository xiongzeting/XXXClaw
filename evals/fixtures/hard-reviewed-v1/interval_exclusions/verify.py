from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'window': [0, 20], 'busy': [[3, 8], [7, 12], [18, 30]], 'reopen': [[5, 10]], 'min_length': 2}),[[0, 3], [5, 10], [12, 18]])
print("PUBLIC_CHECK_OK")
