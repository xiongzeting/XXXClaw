from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'prices': [{'sku': 'x', 'start': 0, 'end': 10, 'cents': 10, 'priority': 1}, {'sku': 'x', 'start': 5, 'end': None, 'cents': 20, 'priority': 1}, {'sku': 'x', 'start': 5, 'end': 8, 'cents': 15, 'priority': 1}], 'orders': [{'id': 'b', 'sku': 'x', 'at': 10, 'qty': 2}, {'id': 'a', 'sku': 'x', 'at': 7, 'qty': 3}, {'id': 'c', 'sku': 'z', 'at': 2, 'qty': 0}]}),[{'id': 'a', 'unit_cents': 15, 'total_cents': 45}, {'id': 'b', 'unit_cents': 20, 'total_cents': 40}, {'id': 'c', 'unit_cents': None, 'total_cents': None}])
print("PUBLIC_CHECK_OK")
