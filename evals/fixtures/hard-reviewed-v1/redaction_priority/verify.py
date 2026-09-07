from solution import solve
def same(a,b):
 if type(a) is not type(b): return False
 if isinstance(a,dict): return a.keys()==b.keys() and all(same(a[k],b[k]) for k in a)
 if isinstance(a,list): return len(a)==len(b) and all(same(x,y) for x,y in zip(a,b))
 return a==b
assert same(solve({'text': 'abc ab abc', 'rules': [{'literal': 'ab', 'replacement': 'abc'}, {'literal': 'abc', 'replacement': 'X'}]}),{'text': 'X abc X', 'counts': {'ab': 1, 'abc': 2}})
print("PUBLIC_CHECK_OK")
