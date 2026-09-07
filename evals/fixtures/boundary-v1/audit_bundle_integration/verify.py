from policy import allowed
assert allowed({'allow':['*:read'],'deny':[]},'alice','read')
print('SMOKE_OK')
