from inventory import reserve
assert reserve('.', 'smoke', [{'sku':'A','qty':1}])['request_id']=='smoke'
print('SMOKE_OK')
