from charge import charge
assert charge({}, 'old', 90)['cents'] == 90
print('SMOKE_OK')
