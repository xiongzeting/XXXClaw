from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'rows': [{'id': 'b', 'note': 'a,b', 'amount_cents': -1}, {'id': '=a', 'note': '@x', 'amount_cents': 105}]}),'id,note,amount\r\n\'=a,\'@x,1.05\r\nb,"a,b",-0.01\r\n')
print("PUBLIC_CHECK_OK")
