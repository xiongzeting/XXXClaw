"""Reviewed delivery contracts with explicit edge semantics and examples."""
SPECS=[]
def add(key,category,split,prompts,tests,bad,reference):
 SPECS.append(dict(key=key,category=category,split=split,prompts=prompts,tests=tests,bad=bad,reference=reference))

add('dependency_waves','tools','development',[
 'solve(data)制定执行批次。data.nodes为job_id到依赖id数组的对象，requested为目标id数组，batch_size为正真整数。只处理requested的依赖闭包，重复目标和重复依赖去重；闭包中的未知节点或环抛ValueError。闭包外无关环不报错。',
 '每轮从未执行节点中找全部依赖已在前面轮次完成的就绪节点，按字典序取最多batch_size个作为本轮；同一轮不能执行依赖本轮其他节点的job。返回对象batches（轮次数组）、order（展平batches），不能修改输入。空requested得到两个空数组。',
 '新增data.disabled数组，缺省空：闭包含disabled节点就ValueError，不能悄悄跳过它或只过滤最终结果；闭包外disabled忽略。batch_size<=0或bool非法。'
],[
 ({'nodes':{'api':['db','cache'],'worker':['db'],'db':[],'cache':['db']},'requested':['api','worker'],'batch_size':2},{'batches':[['db'],['cache','worker'],['api']],'order':['db','cache','worker','api']}),
 ({'nodes':{'b':[],'a':[],'z':['z']},'requested':['b','a','a'],'batch_size':1},{'batches':[['a'],['b']],'order':['a','b']}),
 ({'nodes':{},'requested':[],'batch_size':2},{'batches':[],'order':[]})
],[{'nodes':{'a':['b']},'requested':['a'],'batch_size':1},{'nodes':{'a':['a']},'requested':['a'],'batch_size':1},{'nodes':{'a':[]},'requested':['a'],'batch_size':1,'disabled':['a']},{'nodes':{},'requested':[],'batch_size':True}],'''def solve(d):
 n=d['nodes']; size=d['batch_size']; closure=set(); active=set()
 if type(size)!=int or size<1: raise ValueError('size')
 def visit(x):
  if x in active: raise ValueError('cycle')
  if x in closure: return
  if x not in n or x in d.get('disabled',[]): raise ValueError('node')
  active.add(x)
  for y in n[x]: visit(y)
  active.remove(x); closure.add(x)
 for x in d['requested']: visit(x)
 done=set(); batches=[]
 while done!=closure:
  ready=sorted(x for x in closure-done if set(n[x])<=done)[:size]
  if not ready: raise ValueError('cycle')
  batches.append(ready); done.update(ready)
 return dict(batches=batches,order=[x for b in batches for x in b])
''')

add('event_idempotency','completion','development',[
 'solve(data)重放事件。initial为JSON对象，events数组每项seq为从1开始的真整数、event_id字符串、op=set/delete、key字符串，set另有value。按seq升序执行；set替换key的完整值，delete删除不存在key也成功；返回state、last_seq、event_ids（按已处理seq顺序）。',
 '相同event_id且全部字段完全一致的重复回调只算一次；相同id内容不同、两个不同id占同seq、去重后seq不从1连续、未知op、seq非正整数或bool全部ValueError。空events保留initial、last_seq=0、event_ids=[]。不得修改任何输入对象。',
 '补充：data.ignore_keys缺省空，列出的key仍验证并消费seq和event_id，只是不应用状态变更；不能先过滤事件而造成序号间隙。忽略不存在key合法，结果必须是新对象。'
],[
 ({'initial':{'a':0},'events':[{'seq':2,'event_id':'b','op':'set','key':'a','value':2},{'seq':1,'event_id':'a','op':'set','key':'a','value':1}]},{'state':{'a':2},'last_seq':2,'event_ids':['a','b']}),
 ({'initial':{},'events':[{'seq':1,'event_id':'x','op':'delete','key':'z'}]*2},{'state':{},'last_seq':1,'event_ids':['x']}),
 ({'initial':{'a':0},'events':[{'seq':1,'event_id':'x','op':'set','key':'a','value':False}],'ignore_keys':['a']},{'state':{'a':0},'last_seq':1,'event_ids':['x']}),
 ({'initial':{},'events':[]},{'state':{},'last_seq':0,'event_ids':[]})
],[{'initial':{},'events':[{'seq':2,'event_id':'x','op':'delete','key':'a'}]},{'initial':{},'events':[{'seq':True,'event_id':'x','op':'delete','key':'a'}]},{'initial':{},'events':[{'seq':1,'event_id':'x','op':'delete','key':'a'},{'seq':1,'event_id':'x','op':'delete','key':'b'}]}],'''def solve(d):
 from copy import deepcopy
 ids={}; state=deepcopy(d['initial'])
 for e in d['events']:
  if type(e['seq'])!=int or e['seq']<1 or e['op'] not in ('set','delete'): raise ValueError('event')
  if e['event_id'] in ids and ids[e['event_id']]!=e: raise ValueError('conflict')
  ids[e['event_id']]=e
 ordered=sorted(ids.values(),key=lambda e:e['seq'])
 for i,e in enumerate(ordered,1):
  if e['seq']!=i: raise ValueError('gap')
  if e['key'] in d.get('ignore_keys',[]): continue
  if e['op']=='set': state[e['key']]=deepcopy(e['value'])
  else: state.pop(e['key'],None)
 return dict(state=state,last_seq=len(ordered),event_ids=[e['event_id'] for e in ordered])
''')

add('largest_remainder_caps','tools','retained',[
 'solve(data)按权重分账，amount为非负真整数分，participants数组每项id唯一字符串/weight正真整数。先向下取整amount*weight/权重和；剩余分按小数余数从大到小逐个分配，余数相同按输入数组顺序。输出id到整数分的对象，不修改输入；非空参与人且amount=0全部0。',
 'amount和weight的bool非法；空participants、重复id、负amount、非正weight抛ValueError。必须整数运算以支持超大金额，不允许通过浮点近似。',
 '新需求data.excluded_ids缺省空：先验证完整participants，再排除这些id，只在剩余参与人间分配；输出也只含剩余id，全部被排除抛ValueError。未知排除id忽略，分配后总额严格等于amount。'
],[
 ({'amount':101,'participants':[{'id':'a','weight':1},{'id':'b','weight':2}]},{'a':34,'b':67}),
 ({'amount':2,'participants':[{'id':'z','weight':1},{'id':'a','weight':1},{'id':'b','weight':1}]},{'z':1,'a':1,'b':0}),
 ({'amount':7,'participants':[{'id':'a','weight':1},{'id':'b','weight':3}],'excluded_ids':['a']},{'b':7}),
 ({'amount':0,'participants':[{'id':'a','weight':1}]},{'a':0}),
 ({'amount':100000000000000000001,'participants':[{'id':'a','weight':1},{'id':'b','weight':1}]},{'a':50000000000000000001,'b':50000000000000000000})
],[{'amount':1,'participants':[]},{'amount':1,'participants':[{'id':'a','weight':True}]},{'amount':1,'participants':[{'id':'a','weight':1}],'excluded_ids':['a']}],'''def solve(d):
 amount=d['amount']; rows=d['participants']
 if type(amount)!=int or amount<0 or not rows: raise ValueError('amount')
 if len({r['id'] for r in rows})!=len(rows) or any(type(r['weight'])!=int or r['weight']<=0 for r in rows): raise ValueError('participants')
 rows=[r for r in rows if r['id'] not in d.get('excluded_ids',[])]
 if not rows: raise ValueError('empty')
 den=sum(r['weight'] for r in rows); values=[amount*r['weight']//den for r in rows]
 order=sorted(range(len(rows)),key=lambda i:(-(amount*rows[i]['weight']%den),i))
 for i in order[:amount-sum(values)]: values[i]+=1
 return {r['id']:v for r,v in zip(rows,values)}
''')

add('schema_collision_migration','completion','retained',[
 'solve(data)迁移schema。顶层必须schema=1真整数；递归处理所有对象与数组，name改为display_name，email改为contact。email=null变"unknown"，email缺失则不凭空添加contact；字符串/数字/null/布尔值和未声明字段原样保留。最终仅顶层schema改为2，嵌套schema值不改。',
 '若同对象同时存在name和display_name，两者值相同则移除name保留display_name，不同抛ValueError；email与contact按转换后的email值比较，同值则只保留contact，不同抛ValueError。对象嵌套递归，数组顺序不变，任何失败不修改输入。',
 '追加data顶层的skip_keys数组只作为迁移配置，最终输出保留它。遇到任意对象中列出的key，其整个value子树原样深拷贝，不做改名或碰撞检查；key本身也不改名。其余递归规则不变，顶层schema仍强制改2。'
],[
 ({'schema':1,'users':[{'name':'A','email':None,'meta':{'name':'N'}},{'profile':{'email':'b@x'}}]}, {'schema':2,'users':[{'display_name':'A','contact':'unknown','meta':{'display_name':'N'}},{'profile':{'contact':'b@x'}}]}),
 ({'schema':1,'name':'a','display_name':'a','email':None,'contact':'unknown'}, {'schema':2,'display_name':'a','contact':'unknown'}),
 ({'schema':1,'skip_keys':['raw'],'raw':{'name':'x','display_name':'y'},'inside':{'schema':1,'name':'z'}}, {'schema':2,'skip_keys':['raw'],'raw':{'name':'x','display_name':'y'},'inside':{'schema':1,'display_name':'z'}}),
 ({'schema':1}, {'schema':2})
],[{'schema':True},{'schema':1,'name':'a','display_name':'b'},{'schema':1,'arr':[{'email':None,'contact':'x'}]}],'''def solve(d):
 from copy import deepcopy
 if type(d.get('schema'))!=int or d['schema']!=1: raise ValueError('schema')
 skip=set(d.get('skip_keys',[]))
 def migrate(x):
  if isinstance(x,list): return [migrate(v) for v in x]
  if not isinstance(x,dict): return x
  out={k:deepcopy(v) if k in skip else migrate(v) for k,v in x.items()}
  for old,new in [('name','display_name'),('email','contact')]:
   if old not in out or old in skip: continue
   v=out[old]; v='unknown' if old=='email' and v is None else v
   if new in out and out[new]!=v: raise ValueError('collision')
   del out[old]; out[new]=v
  return out
 result=migrate(d); result['schema']=2; return result
''')

add('reservation_compensation','tools','test',[
 'solve(data)模拟库存事务。stock对象sku->非负真整数，requests有request_id唯一字符串/sku字符串/qty正真整数。按输入顺序处理，在工作副本上扣库存，任何请求未知SKU、库存不足、重复request_id、非法qty全部ValueError，原输入必须毫无变化。输出stock（剩余对象）、allocations（原请求顺序的独立对象拷贝）。',
 '空requests合法；stock中的bool或负数同样非法。失败不能返回部分成功。任何检查不能改变调用方的stock或requests，返回对象也不能与输入共享可变子对象。',
 '增加data.cancelled_ids缺省空：这些request仍要验证id唯一、sku存在、qty合法，但不占库存也不出现在allocations；取消项qty大于库存不算不足。未知取消id忽略。其余请求按原相对顺序处理。'
],[
 ({'stock':{'A':4,'B':2},'requests':[{'request_id':'x','sku':'A','qty':3},{'request_id':'y','sku':'B','qty':1}]}, {'stock':{'A':1,'B':1},'allocations':[{'request_id':'x','sku':'A','qty':3},{'request_id':'y','sku':'B','qty':1}]}),
 ({'stock':{'A':1},'requests':[{'request_id':'x','sku':'A','qty':99},{'request_id':'y','sku':'A','qty':1}],'cancelled_ids':['x']}, {'stock':{'A':0},'allocations':[{'request_id':'y','sku':'A','qty':1}]}),
 ({'stock':{},'requests':[]}, {'stock':{},'allocations':[]})
],[{'stock':{'A':1},'requests':[{'request_id':'x','sku':'A','qty':1},{'request_id':'y','sku':'A','qty':1}]},{'stock':{'A':True},'requests':[]},{'stock':{'A':1},'requests':[{'request_id':'x','sku':'Z','qty':1}],'cancelled_ids':['x']}],'''def solve(d):
 from copy import deepcopy
 stock=deepcopy(d['stock']); rows=d['requests']
 if any(type(v)!=int or v<0 for v in stock.values()): raise ValueError('stock')
 if len({r['request_id'] for r in rows})!=len(rows): raise ValueError('duplicate')
 for r in rows:
  if r['sku'] not in stock or type(r['qty'])!=int or r['qty']<1: raise ValueError('request')
 out=[]
 for r in rows:
  if r['request_id'] in d.get('cancelled_ids',[]): continue
  if stock[r['sku']]<r['qty']: raise ValueError('insufficient')
  stock[r['sku']]-=r['qty']; out.append(deepcopy(r))
 return dict(stock=stock,allocations=out)
''')

add('weighted_interval_plan','completion','test',[
 'solve(data)选择最高收益的非重叠任务。data.jobs每项id唯一字符串/start真整数/end真整数/value真整数，要求start<end，value可以负数。时间段左闭右开，相接可同时选择。返回对象value（总收益整数）、ids（选中任务按start,end,id排序的id数组）。空选择收益0且合法。',
 '多个最优解先选任务数量较少者，仍相同则比较上述排序后id数组，取字典序最小。不得按单任务价值贪心，必须处理跨多个重叠选择的全局最优；输入不变。id重复、端点或value为bool、start>=end抛ValueError。',
 '最新增加data.blacklist缺省空：先验证全部jobs，再排除指定id后优化，未知id忽略。可有负时间；所有value<=0时按少任务规则返回空选择。'
],[
 ({'jobs':[{'id':'long','start':0,'end':10,'value':9},{'id':'a','start':0,'end':5,'value':5},{'id':'b','start':5,'end':10,'value':5}]}, {'value':10,'ids':['a','b']}),
 ({'jobs':[{'id':'z','start':0,'end':10,'value':10},{'id':'a','start':0,'end':5,'value':5},{'id':'b','start':5,'end':10,'value':5}]}, {'value':10,'ids':['z']}),
 ({'jobs':[{'id':'b','start':0,'end':2,'value':2},{'id':'a','start':0,'end':2,'value':2}]}, {'value':2,'ids':['a']}),
 ({'jobs':[{'id':'a','start':-2,'end':0,'value':-1},{'id':'b','start':0,'end':1,'value':0}]}, {'value':0,'ids':[]}),
 ({'jobs':[]}, {'value':0,'ids':[]})
],[{'jobs':[{'id':'a','start':1,'end':1,'value':1}]},{'jobs':[{'id':'a','start':0,'end':1,'value':True}]}],'''def solve(d):
 rows=d['jobs']
 if len({r['id'] for r in rows})!=len(rows): raise ValueError('duplicate')
 if any(any(type(r[k])!=int for k in ('start','end','value')) or r['start']>=r['end'] for r in rows): raise ValueError('job')
 rows=sorted((r for r in rows if r['id'] not in d.get('blacklist',[])),key=lambda r:(r['end'],r['start'],r['id']))
 def key(result):
  value,chosen=result; ordered=sorted(chosen,key=lambda r:(r['start'],r['end'],r['id']))
  return (-value,len(chosen),[r['id'] for r in ordered])
 best=[(0,[])]
 for i,r in enumerate(rows):
  p=max((j+1 for j in range(i) if rows[j]['end']<=r['start']),default=0)
  take=(best[p][0]+r['value'],best[p][1]+[r]); best.append(min(best[-1],take,key=key))
 value,chosen=best[-1]
 return dict(value=value,ids=[r['id'] for r in sorted(chosen,key=lambda r:(r['start'],r['end'],r['id']))])
''')
