from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'stock': {'A': 4, 'B': 2}, 'requests': [{'request_id': 'x', 'sku': 'A', 'qty': 3}, {'request_id': 'y', 'sku': 'B', 'qty': 1}]}),{'stock': {'A': 1, 'B': 1}, 'allocations': [{'request_id': 'x', 'sku': 'A', 'qty': 3}, {'request_id': 'y', 'sku': 'B', 'qty': 1}]})
print("PUBLIC_CHECK_OK")
