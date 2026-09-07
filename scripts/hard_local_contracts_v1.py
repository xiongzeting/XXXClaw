"""Explicit task contracts and independent examples; never model-visible answers."""
SPECS = []


def add(key, category, split, prompts, tests, bad, reference):
    SPECS.append(dict(key=key, category=category, split=split, prompts=prompts,
                      tests=tests, bad=bad, reference=reference))


add('ttl_lru_cache', 'tools', 'development', [
    '实现solve(data)模拟缓存。data.capacity为正整数；data.ops按给定顺序执行，每项t非负整数且不得下降，op为put/get。put还有key字符串/value任意JSON/ttl非负整数，get只有key。输出对象reads（每次get的值，未命中null）和keys（结束后缓存key，从最久未用到最近使用）。每条操作开始先删除expire<=t的项，put的expire=t+ttl。',
    'put覆盖已有key会替换值、过期时间并成为最近使用；get命中也成为最近使用，未命中不改变其他项顺序。超过capacity时淘汰最久未用。必须保留false/0/空字符串，不能把它们当未命中。空ops得到两个空数组，不得修改输入。',
    '最终修订：ttl=0表示删除该key而不插入，也不引起其他淘汰。capacity、t、ttl必须真整数，bool非法；capacity<=0、ttl<0、时间倒退、未知op全部ValueError。验证到坏操作即失败，输入始终不变。'
], [
    ({'capacity':2,'ops':[{'t':0,'op':'put','key':'a','value':0,'ttl':10},{'t':0,'op':'put','key':'b','value':False,'ttl':10},{'t':1,'op':'get','key':'a'},{'t':2,'op':'put','key':'c','value':'','ttl':10},{'t':3,'op':'get','key':'b'},{'t':10,'op':'get','key':'a'}]}, {'reads':[0,None,None],'keys':['c']}),
    ({'capacity':1,'ops':[{'t':0,'op':'put','key':'a','value':1,'ttl':1},{'t':1,'op':'get','key':'a'}]}, {'reads':[None],'keys':[]}),
    ({'capacity':1,'ops':[{'t':0,'op':'put','key':'a','value':1,'ttl':10},{'t':0,'op':'put','key':'b','value':2,'ttl':0}]}, {'reads':[],'keys':['a']}),
    ({'capacity':2,'ops':[]}, {'reads':[],'keys':[]})
], [{'capacity':0,'ops':[]},{'capacity':1,'ops':[{'t':2,'op':'get','key':'a'},{'t':1,'op':'get','key':'a'}]},{'capacity':1,'ops':[{'t':0,'op':'put','key':'a','value':1,'ttl':True}]}], '''def solve(d):
 from collections import OrderedDict
 from copy import deepcopy
 cap=d['capacity']
 if type(cap)!=int or cap<=0: raise ValueError('capacity')
 cache=OrderedDict(); reads=[]; previous=0
 for op in d['ops']:
  t=op['t']
  if type(t)!=int or t<previous: raise ValueError('time')
  previous=t
  for key in list(cache):
   if cache[key][1]<=t: del cache[key]
  key=op['key']
  if op['op']=='get':
   reads.append(deepcopy(cache[key][0]) if key in cache else None)
   if key in cache: cache.move_to_end(key)
  elif op['op']=='put':
   ttl=op['ttl']
   if type(ttl)!=int or ttl<0: raise ValueError('ttl')
   cache.pop(key,None)
   if ttl:
    cache[key]=(deepcopy(op['value']),t+ttl)
    while len(cache)>cap: cache.popitem(last=False)
  else: raise ValueError('op')
 return dict(reads=reads,keys=list(cache))
''')

add('sessionize_events', 'completion', 'development', [
    '实现solve(data)会话切分。data.events数组，每项id字符串/user字符串/t真整数>=0/value真整数；data.gap真整数>=0。完全相同id重复只保留一次，同id任一字段不同抛ValueError。按user分组再按(t,id)升序，相邻事件时间差>gap开启新会话，等于gap仍同会话。',
    '输出数组按user再start再first_id排序。每项user/start/end/first_id/event_ids/total；event_ids为该会话(t,id)顺序，total为value之和。负value合法，0和空user合法，不修改输入，空events返回空数组。',
    '新增data.excluded_users数组，缺省空。先全量校验和id冲突去重，再排除用户；排除用户的坏记录也必须抛错。用户名精确匹配，无大小写折叠。'
], [
    ({'gap':5,'events':[{'id':'c','user':'u','t':11,'value':-2},{'id':'b','user':'u','t':5,'value':3},{'id':'a','user':'u','t':0,'value':2},{'id':'d','user':'v','t':0,'value':0}]}, [{'user':'u','start':0,'end':5,'first_id':'a','event_ids':['a','b'],'total':5},{'user':'u','start':11,'end':11,'first_id':'c','event_ids':['c'],'total':-2},{'user':'v','start':0,'end':0,'first_id':'d','event_ids':['d'],'total':0}]),
    ({'gap':0,'events':[{'id':'b','user':'','t':1,'value':1},{'id':'a','user':'','t':1,'value':2},{'id':'a','user':'','t':1,'value':2}]}, [{'user':'','start':1,'end':1,'first_id':'a','event_ids':['a','b'],'total':3}]),
    ({'gap':3,'events':[{'id':'a','user':'u','t':0,'value':1}],'excluded_users':['u']}, []),
    ({'gap':0,'events':[]}, [])
], [{'gap':-1,'events':[]},{'gap':1,'events':[{'id':'a','user':'u','t':0,'value':1},{'id':'a','user':'u','t':1,'value':1}],'excluded_users':['u']},{'gap':1,'events':[{'id':'a','user':'u','t':False,'value':1}]}], '''def solve(d):
 gap=d['gap']; unique={}
 if type(gap)!=int or gap<0: raise ValueError('gap')
 for r in d['events']:
  if type(r['t'])!=int or r['t']<0 or type(r['value'])!=int: raise ValueError('event')
  if r['id'] in unique and unique[r['id']]!=r: raise ValueError('conflict')
  unique[r['id']]=r
 rows=sorted((r for r in unique.values() if r['user'] not in d.get('excluded_users',[])),key=lambda r:(r['user'],r['t'],r['id']))
 out=[]
 for r in rows:
  if not out or out[-1]['user']!=r['user'] or r['t']-out[-1]['end']>gap:
   out.append(dict(user=r['user'],start=r['t'],end=r['t'],first_id=r['id'],event_ids=[],total=0))
  out[-1]['end']=r['t']; out[-1]['event_ids'].append(r['id']); out[-1]['total']+=r['value']
 return out
''')

add('json_pointer_transaction', 'tools', 'retained', [
    '实现solve(data)：data.document任意JSON，data.operations数组，按顺序执行op为set/remove/test；每项path为JSON Pointer字符串，set/test有value。返回修改后的深拷贝，绝不修改输入。空path指根；非空必须/开头，段内~1解码/、~0解码~，其他~转义非法。',
    '对象set允许新增末尾key，但中间路径必须存在；remove/test目标必须存在。数组索引只接受0或无前导零的正整数字符串；set仅替换已有索引，不插入，remove删除后后项左移；"-"在所有操作中非法。不能穿过标量。根set替换整个document；根remove非法。错误都抛ValueError，失败输入保持原样。',
    'test要求JSON类型和值严格相同，bool与整数不同；对象键顺序不影响，数组顺序影响。所有op名字需合法；空operations返回独立深拷贝。新增校验：路径/01作为对象key合法，只有当父节点是数组时禁止前导零。'
], [
    ({'document':{'a/b':{'~x':[1,2]},'01':'old'},'operations':[{'op':'set','path':'/a~1b/~0x/1','value':3},{'op':'set','path':'/01','value':'new'},{'op':'remove','path':'/a~1b/~0x/0'}]}, {'a/b':{'~x':[3]},'01':'new'}),
    ({'document':1,'operations':[{'op':'set','path':'','value':{'x':False}},{'op':'test','path':'/x','value':False}]}, {'x':False}),
    ({'document':{'a':[1]},'operations':[]}, {'a':[1]}),
    ({'document':{},'operations':[{'op':'set','path':'/x','value':None}]}, {'x':None})
], [{'document':[1,2],'operations':[{'op':'set','path':'/01','value':3}]},{'document':{'x':True},'operations':[{'op':'test','path':'/x','value':1}]},{'document':{},'operations':[{'op':'remove','path':''}]},{'document':{},'operations':[{'op':'set','path':'/~2','value':1}]}], '''def solve(d):
 import copy,re
 root=copy.deepcopy(d['document'])
 def equal(a,b):
  if type(a)!=type(b): return False
  if isinstance(a,dict): return a.keys()==b.keys() and all(equal(a[k],b[k]) for k in a)
  if isinstance(a,list): return len(a)==len(b) and all(equal(x,y) for x,y in zip(a,b))
  return a==b
 def index(parent,k):
  if k=='-': raise ValueError('index')
  if isinstance(parent,list):
   if re.fullmatch('0|[1-9][0-9]*',k) is None or int(k)>=len(parent): raise ValueError('index')
   return int(k)
  if isinstance(parent,dict): return k
  raise ValueError('parent')
 for op in d['operations']:
  p=op['path']; kind=op['op']
  if kind not in ('set','remove','test') or not isinstance(p,str) or (p and not p.startswith('/')): raise ValueError('op')
  if re.search('~(?![01])',p): raise ValueError('escape')
  if not p:
   if kind=='set': root=copy.deepcopy(op['value'])
   elif kind=='remove' or not equal(root,op['value']): raise ValueError('root')
   continue
  segments=[s.replace('~1','/').replace('~0','~') for s in p[1:].split('/')]; parent=root
  try:
   for s in segments[:-1]: parent=parent[index(parent,s)]
   k=index(parent,segments[-1])
   if kind=='set': parent[k]=copy.deepcopy(op['value'])
   elif kind=='remove': del parent[k]
   elif not equal(parent[k],op['value']): raise ValueError('test')
  except (KeyError,IndexError,TypeError): raise ValueError('path')
 return root
''')

add('temporal_price_join', 'completion', 'retained', [
    '实现solve(data)关联订单与价格历史。data.prices数组，每项sku字符串/start非负整数/end非负整数或null/cents非负整数/priority整数；data.orders数组，每项id字符串/sku字符串/at非负整数/qty非负整数。价格区间[start,end)，end=null无限。对每个订单找包含at且sku相同的价格，按priority最大、start最大、cents最小选一个。',
    '输出按order.id升序的数组，每项id/unit_cents/total_cents；无匹配两个金额都是null，qty=0且有匹配时total=0。价格重叠合法。end<=start非法；上述所有数值bool非法。订单id重复、负qty等抛ValueError。不修改输入。',
    '新增data.blocked_skus缺省空，对其中sku订单一律两个金额null；但仍全量验证所有订单和价格，不能因为被屏蔽而跳过坏数据。价格未被任何订单引用也要验证。'
], [
    ({'prices':[{'sku':'x','start':0,'end':10,'cents':10,'priority':1},{'sku':'x','start':5,'end':None,'cents':20,'priority':1},{'sku':'x','start':5,'end':8,'cents':15,'priority':1}], 'orders':[{'id':'b','sku':'x','at':10,'qty':2},{'id':'a','sku':'x','at':7,'qty':3},{'id':'c','sku':'z','at':2,'qty':0}]}, [{'id':'a','unit_cents':15,'total_cents':45},{'id':'b','unit_cents':20,'total_cents':40},{'id':'c','unit_cents':None,'total_cents':None}]),
    ({'prices':[{'sku':'x','start':0,'end':None,'cents':0,'priority':-2}],'orders':[{'id':'a','sku':'x','at':0,'qty':0}]}, [{'id':'a','unit_cents':0,'total_cents':0}]),
    ({'prices':[],'orders':[{'id':'a','sku':'x','at':0,'qty':2}],'blocked_skus':['x']}, [{'id':'a','unit_cents':None,'total_cents':None}]),
    ({'prices':[],'orders':[]}, [])
], [{'prices':[{'sku':'unused','start':1,'end':1,'cents':1,'priority':0}],'orders':[]},{'prices':[],'orders':[{'id':'a','sku':'x','at':0,'qty':False}]},{'prices':[],'orders':[{'id':'a','sku':'x','at':0,'qty':1}]*2}], '''def solve(d):
 for p in d['prices']:
  if any(type(p[k])!=int for k in ('start','cents','priority')) or p['start']<0 or p['cents']<0: raise ValueError('price')
  if p['end'] is not None and (type(p['end'])!=int or p['end']<=p['start']): raise ValueError('interval')
 if len({o['id'] for o in d['orders']})!=len(d['orders']): raise ValueError('duplicate')
 for o in d['orders']:
  if any(type(o[k])!=int or o[k]<0 for k in ('at','qty')): raise ValueError('order')
 out=[]
 for o in sorted(d['orders'],key=lambda o:o['id']):
  options=[p for p in d['prices'] if p['sku']==o['sku'] and p['start']<=o['at'] and (p['end'] is None or o['at']<p['end'])] if o['sku'] not in d.get('blocked_skus',[]) else []
  price=min(options,key=lambda p:(-p['priority'],-p['start'],p['cents']))['cents'] if options else None
  out.append(dict(id=o['id'],unit_cents=price,total_cents=price*o['qty'] if price is not None else None))
 return out
''')

add('csv_formula_export', 'tools', 'test', [
    '实现solve(data)返回CSV字符串。data.rows每项id字符串/note字符串/amount_cents真整数，id必须唯一。表头固定id,note,amount，按id的Unicode字典序输出，金额用精确两位小数字符串（负金额合法），不使用浮点。CSV逗号分隔、双引号引用，字段有逗号/双引号/CR/LF才加引号，字段双引号双写。每行CRLF且末尾也有CRLF。',
    'CSV导出防公式：id和note以 =、+、-、@ 中任一字符开头时，前置单引号；只检查原始首字符，不去除空格，已有单引号不再加。amount列属于可信数字，不加防公式单引号。Unicode、空字符串、多行note都保留原样。',
    '新增data.omit_ids数组缺省空：验证所有行后去掉这些id，再排序。重复id及amount_cents为bool/非整数均ValueError，即使该行被omit。空结果仍输出表头。不得修改输入。'
], [
    ({'rows':[{'id':'b','note':'a,b','amount_cents':-1},{'id':'=a','note':'@x','amount_cents':105}]}, "id,note,amount\r\n'=a,'@x,1.05\r\nb,\"a,b\",-0.01\r\n"),
    ({'rows':[{'id':'a','note':'x\n"y"','amount_cents':0}]}, 'id,note,amount\r\na,"x\n""y""",0.00\r\n'),
    ({'rows':[{'id':'a','note':' =not_formula','amount_cents':100}],'omit_ids':['a']}, 'id,note,amount\r\n'),
    ({'rows':[]}, 'id,note,amount\r\n')
], [{'rows':[{'id':'a','note':'','amount_cents':True}]},{'rows':[{'id':'a','note':'','amount_cents':1}]*2,'omit_ids':['a']}], '''def solve(d):
 import io,csv
 rows=d['rows']
 if len({r['id'] for r in rows})!=len(rows): raise ValueError('duplicate')
 if any(type(r['amount_cents'])!=int for r in rows): raise ValueError('amount')
 def safe(s): return "'"+s if s and s[0] in '=+-@' else s
 out=io.StringIO(newline=''); w=csv.writer(out,lineterminator='\\r\\n'); w.writerow(['id','note','amount'])
 for r in sorted(rows,key=lambda r:r['id']):
  if r['id'] in d.get('omit_ids',[]): continue
  n=r['amount_cents']; a=abs(n); amount=('-' if n<0 else '')+str(a//100)+'.'+str(a%100).zfill(2)
  w.writerow([safe(r['id']),safe(r['note']),amount])
 return out.getvalue()
''')

add('cursor_page_contract', 'completion', 'test', [
    '实现solve(data)稳定分页。data.records每项id唯一字符串/score整数/visible布尔；data.limit为正整数；data.after缺省null或对象score/id。只对visible=true记录按score降序再id升序排列，返回对象items（原记录的独立拷贝数组）、next（下一页游标或null）、has_more布尔。',
    'after表示已消费的排序位置，即使这条记录已经被删除也仍按比较关系跳过它及之前记录；不能按数组查找游标。limit之外还有合资格记录时has_more才true，next是本页最后记录的score/id；无更多时next为null。空结果items=[]、has_more=false、next=null。',
    '最终规则：data.excluded_ids缺省空，先全量验证id唯一、score真整数（bool非法）、visible严格bool，再按visible和excluded过滤。limit也必须正真整数。after.score真整数、after.id字符串；不修改输入，未知record扩展字段保留。'
], [
    ({'records':[{'id':'b','score':10,'visible':True,'extra':0},{'id':'a','score':10,'visible':True},{'id':'c','score':9,'visible':True}],'limit':1}, {'items':[{'id':'a','score':10,'visible':True}],'next':{'score':10,'id':'a'},'has_more':True}),
    ({'records':[{'id':'a','score':10,'visible':True},{'id':'c','score':9,'visible':True}],'after':{'score':10,'id':'b'},'limit':2}, {'items':[{'id':'c','score':9,'visible':True}],'next':None,'has_more':False}),
    ({'records':[{'id':'a','score':0,'visible':False},{'id':'b','score':-1,'visible':True}],'limit':1,'excluded_ids':['b']}, {'items':[],'next':None,'has_more':False}),
    ({'records':[],'limit':1}, {'items':[],'next':None,'has_more':False})
], [{'records':[],'limit':True},{'records':[{'id':'a','score':True,'visible':True}],'limit':1},{'records':[{'id':'a','score':1,'visible':False}]*2,'limit':1}], '''def solve(d):
 from copy import deepcopy
 limit=d['limit']; rows=d['records']; after=d.get('after')
 if type(limit)!=int or limit<=0: raise ValueError('limit')
 if len({r['id'] for r in rows})!=len(rows): raise ValueError('duplicate')
 if any(type(r['score'])!=int or type(r['visible'])!=bool for r in rows): raise ValueError('record')
 if after is not None and (type(after['score'])!=int or not isinstance(after['id'],str)): raise ValueError('cursor')
 eligible=sorted((r for r in rows if r['visible'] and r['id'] not in d.get('excluded_ids',[]) and (after is None or (-r['score'],r['id'])>(-after['score'],after['id']))),key=lambda r:(-r['score'],r['id']))
 page=deepcopy(eligible[:limit]); more=len(eligible)>limit
 return dict(items=page,next={'score':page[-1]['score'],'id':page[-1]['id']} if more else None,has_more=more)
''')


add('segmented_router', 'compression', 'development', [
    '实现 solve(data)：data 有 rules 和 requests 两个数组。rule 字段 id、tenant、prefix 为字符串，priority 为整数，enabled 为布尔；request 字段 tenant、path。为每个请求返回选中规则 id 或 null，顺序与请求一致。tenant="*" 可匹配任何租户；其余必须精确相等。prefix="/" 匹配所有路径，其余匹配相同路径或 prefix+"/" 开头，不能把 /api 匹配到 /apix。',
    '补充：每个请求仅考虑 enabled=true 的规则。多个匹配规则先取最长 prefix，长度相同取 priority 最大，再相同按 id 字典序最小。精确租户没有额外优先级；不要修改输入。rules.id 重复抛 ValueError；空 rules 的每个请求结果为 null。',
    '最新变更：data 还可有 revoked 数组，缺省为空，列出的规则 id 必须先排除，不能选中后再返回 null；因此应继续选择下一候选。未知 revoked id 忽略。空 requests 返回空数组，仍要验证重复规则 id。其余约束保持。'
], [
    ({'rules': [{'id':'root','tenant':'*','prefix':'/','priority':100,'enabled':True},{'id':'api','tenant':'*','prefix':'/api','priority':1,'enabled':True},{'id':'z','tenant':'a','prefix':'/api','priority':2,'enabled':True},{'id':'a','tenant':'*','prefix':'/api','priority':2,'enabled':True}], 'requests':[{'tenant':'a','path':'/api/x'},{'tenant':'b','path':'/apix'},{'tenant':'b','path':'/api'}]}, ['a','root','a']),
    ({'rules':[{'id':'r','tenant':'*','prefix':'/','priority':0,'enabled':True},{'id':'x','tenant':'a','prefix':'/x','priority':9,'enabled':True}], 'requests':[{'tenant':'a','path':'/x'}], 'revoked':['x','unknown']}, ['r']),
    ({'rules':[{'id':'x','tenant':'a','prefix':'/','priority':0,'enabled':False}], 'requests':[{'tenant':'a','path':'/'}]}, [None]),
    ({'rules':[], 'requests':[]}, [])
], [{'rules':[{'id':'x','tenant':'a','prefix':'/','priority':0,'enabled':True},{'id':'x','tenant':'a','prefix':'/','priority':0,'enabled':True}],'requests':[]}], '''def solve(d):
 rules=d['rules']; ids=[r['id'] for r in rules]
 if len(ids)!=len(set(ids)): raise ValueError('duplicate')
 out=[]
 for q in d['requests']:
  eligible=[r for r in rules if r['enabled'] and r['id'] not in d.get('revoked',[]) and r['tenant'] in ('*',q['tenant']) and (r['prefix']=='/' or q['path']==r['prefix'] or q['path'].startswith(r['prefix']+'/'))]
  eligible.sort(key=lambda r:(-len(r['prefix']),-r['priority'],r['id']))
  out.append(eligible[0]['id'] if eligible else None)
 return out
''')

add('tiered_invoice', 'compression', 'development', [
    '实现 solve(data) 给电量账单计费。data.lines 数组，每条有 id 字符串、units 非负整数、exempt 布尔。每条独立阶梯：前10单位每单位100分，第11至20单位80分，其余50分。输出对象 lines（按id升序，每项id/base_cents/tax_cents/total_cents）及 grand_total_cents。禁止用浮点金额。',
    '补充税：非 exempt 条目税为 base_cents 的 7%，逐条四舍五入到整数分（半分向上）；exempt税为0。所有lines id必须唯一，负units或非整数units（含bool）抛ValueError，不修改输入。空列表总额0。',
    '变更：data.discount_cents 缺省0，必须非负整数且bool非法；先按id升序将折扣依次抵扣每条基础费用，最低0，多余折扣丢弃。税基改为抵扣后的基础费用，输出base_cents也为抵扣后值。其他规则保持。'
], [
    ({'lines':[{'id':'b','units':21,'exempt':False},{'id':'a','units':1,'exempt':False}], 'discount_cents':150}, {'lines':[{'id':'a','base_cents':0,'tax_cents':0,'total_cents':0},{'id':'b','base_cents':1800,'tax_cents':126,'total_cents':1926}], 'grand_total_cents':1926}),
    ({'lines':[{'id':'a','units':1,'exempt':False}], 'discount_cents':50}, {'lines':[{'id':'a','base_cents':50,'tax_cents':4,'total_cents':54}], 'grand_total_cents':54}),
    ({'lines':[{'id':'z','units':11,'exempt':True}], 'discount_cents':0}, {'lines':[{'id':'z','base_cents':1080,'tax_cents':0,'total_cents':1080}], 'grand_total_cents':1080}),
    ({'lines':[], 'discount_cents':999}, {'lines':[], 'grand_total_cents':0})
], [{'lines':[{'id':'x','units':True,'exempt':False}]},{'lines':[],'discount_cents':-1},{'lines':[{'id':'x','units':0,'exempt':False}]*2}], '''def solve(d):
 rows=d['lines']; disc=d.get('discount_cents',0)
 if type(disc)!=int or disc<0: raise ValueError('discount')
 if len({r['id'] for r in rows})!=len(rows): raise ValueError('duplicate')
 for r in rows:
  if type(r['units'])!=int or r['units']<0: raise ValueError('units')
 out=[]
 for r in sorted(rows,key=lambda r:r['id']):
  n=r['units']; base=min(n,10)*100+min(max(n-10,0),10)*80+max(n-20,0)*50
  used=min(base,disc); base-=used; disc-=used
  tax=0 if r['exempt'] else (base*7+50)//100
  out.append(dict(id=r['id'],base_cents=base,tax_cents=tax,total_cents=base+tax))
 return dict(lines=out,grand_total_cents=sum(r['total_cents'] for r in out))
''')

add('interval_exclusions', 'compression', 'retained', [
    '实现 solve(data) 返回可用维护时间段。data.window 是 [start,end] 整数分钟，半开区间；data.busy 是若干同型区间。先把busy裁剪到window，取它们并集，再输出剩余区间，起点升序。相接busy段视为连续。不得修改输入，零长度段无影响。',
    '补充：所有端点必须是真整数（bool不算），任何start>end抛ValueError，即使该busy完全落在window外也要检查。window相同端点返回空数组。结果不得有重叠或零长度。',
    '新增data.reopen（缺省空数组）：这些区间从busy并集中扣除，允许重开局部维护窗口；不是从最终可用区间扣除。reopen同样裁剪、同样验证。最后只保留长度至少data.min_length的可用段；min_length缺省1，必须正整数。'
], [
    ({'window':[0,20],'busy':[[3,8],[7,12],[18,30]],'reopen':[[5,10]],'min_length':2}, [[0,3],[5,10],[12,18]]),
    ({'window':[0,10],'busy':[[0,5],[5,10]],'reopen':[[4,6]]}, [[4,6]]),
    ({'window':[0,10],'busy':[[20,30]],'reopen':[],'min_length':11}, []),
    ({'window':[5,5],'busy':[]}, [])
], [{'window':[0,10],'busy':[[99,90]]},{'window':[0,10],'busy':[],'min_length':0},{'window':[False,10],'busy':[]}], '''def solve(d):
 def valid(iv):
  if len(iv)!=2 or any(type(x)!=int for x in iv) or iv[0]>iv[1]: raise ValueError('interval')
 valid(d['window']); a,b=d['window']; busy=d['busy']; opened=d.get('reopen',[])
 for iv in busy+opened: valid(iv)
 minimum=d.get('min_length',1)
 if type(minimum)!=int or minimum<1: raise ValueError('length')
 points=sorted({a,b}|{max(a,min(b,x)) for iv in busy+opened for x in iv})
 out=[]
 for x,y in zip(points,points[1:]):
  blocked=any(s<=x<e for s,e in busy) and not any(s<=x<e for s,e in opened)
  if x<y and not blocked:
   if out and out[-1][1]==x: out[-1][1]=y
   else: out.append([x,y])
 return [iv for iv in out if iv[1]-iv[0]>=minimum]
''')

add('redaction_priority', 'compression', 'retained', [
    '实现 solve(data)：data.text 为Unicode字符串，data.rules是literal字符串与replacement字符串的对象数组；做字面替换，不是正则。返回对象 text 与 counts（每个原始literal对应实际命中次数）。不能修改输入，空rules原样返回。',
    '替换不可级联：新replacement文本不能再次命中规则。从原始文本左到右扫描，同一起点多个literal匹配时取最长，其余被覆盖的不计数，命中后跳过原始匹配长度。空literal、重复literal抛ValueError，即使text为空也验证。',
    '最新需求：data.protected 是原始字符串字符索引的半开区间数组，缺省空；任何匹配只要与保护区重叠就不能使用，继续尝试同一起点更短且不重叠的规则。区间端点为真整数、0<=start<=end<=len(text)，不合法抛ValueError。保护区可重叠，counts必须包括零命中的规则。'
], [
    ({'text':'abc ab abc','rules':[{'literal':'ab','replacement':'abc'},{'literal':'abc','replacement':'X'}]}, {'text':'X abc X','counts':{'ab':1,'abc':2}}),
    ({'text':'abc abc','rules':[{'literal':'abc','replacement':'X'},{'literal':'ab','replacement':'Y'}],'protected':[[2,3]]}, {'text':'Yc X','counts':{'abc':1,'ab':1}}),
    ({'text':'猫猫a','rules':[{'literal':'猫','replacement':'犬'}],'protected':[[0,1]]}, {'text':'猫犬a','counts':{'猫':1}}),
    ({'text':'unchanged','rules':[]}, {'text':'unchanged','counts':{}})
], [{'text':'','rules':[{'literal':'','replacement':'x'}]},{'text':'x','rules':[],'protected':[[0,2]]},{'text':'x','rules':[{'literal':'x','replacement':'a'},{'literal':'x','replacement':'b'}]}], '''def solve(d):
 text=d['text']; rules=d['rules']; words=[r['literal'] for r in rules]
 if any(not w for w in words) or len(set(words))!=len(words): raise ValueError('rules')
 protected=d.get('protected',[])
 for iv in protected:
  if len(iv)!=2 or any(type(x)!=int for x in iv) or not 0<=iv[0]<=iv[1]<=len(text): raise ValueError('range')
 counts={w:0 for w in words}; out=[]; i=0
 while i<len(text):
  candidates=[r for r in rules if text.startswith(r['literal'],i) and not any(i<e and s<i+len(r['literal']) for s,e in protected)]
  if candidates:
   r=max(candidates,key=lambda r:len(r['literal'])); out.append(r['replacement']); counts[r['literal']]+=1; i+=len(r['literal'])
  else: out.append(text[i]); i+=1
 return dict(text=''.join(out),counts=counts)
''')

add('version_resolution', 'compression', 'test', [
    '实现 solve(data) 为一批组件选择版本。data.catalog 是 name到版本字符串数组的对象，版本格式恰好 MAJOR.MINOR.PATCH，三个非负十进制整数、不允许前导零（单个0合法）。data.requirements数组，每项name/min/max，min包含、max不包含；max可以null。输出name到选中最高版本的对象。比较用整数元组，不按字符串。',
    '同组件多个requirements取交集，不是最后一个覆盖；候选重复去重。name不存在或交集没有候选抛ValueError，所有候选/边界字符串都要先验证，即使所属组件没被请求。min>=max也是ValueError；空requirements返回空对象。输入不得修改。',
    '追加data.yanked对象（缺省{}），每组件给撤回版本数组，撤回的候选不可选，数组内版本同样验证。撤回未知组件合法且忽略其匹配作用。输出只含被请求组件，版本保持规范原拼写。'
], [
    ({'catalog':{'a':['1.2.0','1.10.0','2.0.0'],'b':['0.0.1']},'requirements':[{'name':'a','min':'1.0.0','max':'2.0.0'}]}, {'a':'1.10.0'}),
    ({'catalog':{'a':['1.0.0','1.2.0','1.3.0']},'requirements':[{'name':'a','min':'1.0.0','max':None},{'name':'a','min':'1.1.0','max':'1.3.0'}]}, {'a':'1.2.0'}),
    ({'catalog':{'a':['1.0.0','1.1.0']},'requirements':[{'name':'a','min':'0.0.0','max':None}],'yanked':{'a':['1.1.0']}}, {'a':'1.0.0'}),
    ({'catalog':{},'requirements':[]}, {})
], [{'catalog':{'unused':['01.0.0']},'requirements':[]},{'catalog':{},'requirements':[{'name':'x','min':'0.0.0','max':None}]},{'catalog':{'a':['1.0.0']},'requirements':[{'name':'a','min':'1.0.0','max':'1.0.0'}]}], '''def solve(d):
 import re
 def version(v):
  if not isinstance(v,str) or re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)',v) is None: raise ValueError('version')
  return tuple(map(int,v.split('.')))
 for vs in d['catalog'].values():
  for v in vs: version(v)
 for vs in d.get('yanked',{}).values():
  for v in vs: version(v)
 groups={}
 for r in d['requirements']:
  lo=version(r['min']); hi=version(r['max']) if r['max'] is not None else None
  if hi is not None and lo>=hi: raise ValueError('range')
  groups.setdefault(r['name'],[]).append((lo,hi))
 out={}
 for name,ranges in groups.items():
  candidates=[v for v in d['catalog'].get(name,[]) if v not in d.get('yanked',{}).get(name,[]) and all(version(v)>=lo and (hi is None or version(v)<hi) for lo,hi in ranges)]
  if not candidates: raise ValueError('unsatisfied')
  out[name]=max(candidates,key=version)
 return out
''')

add('shipping_caps', 'compression', 'test', [
    '实现 solve(data) 报价。data.packages每项id、weight_g真整数>=0、zone为local/remote、fragile布尔。每包首1000g（含0g）500分，超出部分每开始1000g加200分；remote每包另加300分。返回按id排序的数组，每项id/shipping_cents。不修改输入。',
    '易碎包逐包加150分，id重复、重量非法（bool非法）、未知zone抛ValueError。data.free_ids缺省空，列出的包完全免运费，未知id忽略，但免运费包也必须先验证数据。空packages返回空数组。',
    '最后加入data.cap_cents缺省null；非null时必须非负真整数。若总费超过cap，按id升序依次保留费用直到额度耗尽，后续包0，某包可部分收费。免运费先于cap处理。'
], [
    ({'packages':[{'id':'b','weight_g':1001,'zone':'remote','fragile':True},{'id':'a','weight_g':0,'zone':'local','fragile':False}],'cap_cents':1000}, [{'id':'a','shipping_cents':500},{'id':'b','shipping_cents':500}]),
    ({'packages':[{'id':'x','weight_g':2000,'zone':'remote','fragile':True}]}, [{'id':'x','shipping_cents':1150}]),
    ({'packages':[{'id':'x','weight_g':2001,'zone':'local','fragile':False}],'free_ids':['x'],'cap_cents':0}, [{'id':'x','shipping_cents':0}]),
    ({'packages':[]}, [])
], [{'packages':[{'id':'x','weight_g':-1,'zone':'local','fragile':False}],'free_ids':['x']},{'packages':[],'cap_cents':False},{'packages':[{'id':'x','weight_g':0,'zone':'moon','fragile':False}]}], '''def solve(d):
 rows=d['packages']; cap=d.get('cap_cents')
 if cap is not None and (type(cap)!=int or cap<0): raise ValueError('cap')
 if len({r['id'] for r in rows})!=len(rows): raise ValueError('duplicate')
 for r in rows:
  if type(r['weight_g'])!=int or r['weight_g']<0 or r['zone'] not in ('local','remote'): raise ValueError('package')
 out=[]
 for r in sorted(rows,key=lambda r:r['id']):
  fee=500+(max(r['weight_g']-1000,0)+999)//1000*200+300*(r['zone']=='remote')+150*r['fragile']
  if r['id'] in d.get('free_ids',[]): fee=0
  if cap is not None: fee=min(fee,cap); cap-=fee
  out.append(dict(id=r['id'],shipping_cents=fee))
 return out
''')
