from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'lines': [{'id': 'b', 'units': 21, 'exempt': False}, {'id': 'a', 'units': 1, 'exempt': False}], 'discount_cents': 150}),{'lines': [{'id': 'a', 'base_cents': 0, 'tax_cents': 0, 'total_cents': 0}, {'id': 'b', 'base_cents': 1800, 'tax_cents': 126, 'total_cents': 1926}], 'grand_total_cents': 1926})
print("PUBLIC_CHECK_OK")
