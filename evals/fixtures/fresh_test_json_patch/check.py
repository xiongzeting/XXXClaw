import json
assert json.load(open('settings.json')) == {'notifications':{'email':False,'sms':True},'theme':'dark'}
print('CHECK_OK')
