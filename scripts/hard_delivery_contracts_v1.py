"""Pure-function hard delivery contracts."""
from __future__ import annotations
import subprocess,sys,tempfile
from pathlib import Path

def dependency(data):
 n,r,z=data['nodes'],data['requested'],data['batch_size']; seen=set(); out=[]
 if not isinstance(z,int) or z<1: raise ValueError('batch_size')
 def v(x,stack):
  if x in stack: raise ValueError('cycle '+','.join(stack+[x]))
  if x in seen:return
  if x not in n:raise ValueError('unknown')
  for y in n[x]:v(y,stack+[x])
  seen.add(x);out.append(x)
 for x in r:v(x,[])
 return {'order':out,'batches':[out[i:i+z] for i in range(0,len(out),z)]}

def events(data):
 state=dict(data.get('initial',{})); ids={}; seq={}
 for e in sorted(data['events'],key=lambda x:x['seq']):
  i=e['event_id']
  if i in ids:
   if ids[i]!=e:raise ValueError('conflict')
   continue
  if e['seq']!=len(seq)+1 or e['seq'] in seq:raise ValueError('gap')
  if e['op'] not in ('set','delete'):raise ValueError('op')
  if e['op']=='set':state[e['key']]=e['value']
  else:state.pop(e['key'],None)
  ids[i]=dict(e);seq[e['seq']]=i
 return {'state':state,'last_seq':len(seq),'event_ids':list(ids)}

def split(data):
 a=data['amounts']; p=data['shares']['participants']
 if not a or any(type(x)is not int or x<0 for x in a) or not p:raise ValueError('input')
 if any(type(x['weight'])is not int or x['weight']<=0 for x in p):raise ValueError('weight')
 t=sum(a); den=sum(x['weight'] for x in p); q=[t*x['weight']//den for x in p]; rem=t-sum(q)
 for i in sorted(range(len(p)),key=lambda i:(-((t*p[i]['weight'])%den),i))[:rem]:q[i]+=1
 return {'total_cents':t,'allocations':{x['id']:v for x,v in zip(p,q)}}

def migrate(data):
 if data.get('schema')!=1:raise ValueError('schema')
 def c(x):
  if isinstance(x,list):return [c(y) for y in x]
  if not isinstance(x,dict):return x
  o={k:c(v) for k,v in x.items()}
  if 'name'in o:o['display_name']=o.pop('name')
  if 'email'in o:
   e=o.pop('email');o['contact']='unknown' if e is None else e
  return o
 o=c(data);o['schema']=2;return o

def reserve(data):
 s=dict(data['stock']); seen=set(); out=[]
 for r in data['requests']:
  if r['request_id']in seen or type(r['qty'])is not int or r['qty']<=0 or r['sku']not in s or s[r['sku']]<r['qty']:raise ValueError('request')
  seen.add(r['request_id']);s[r['sku']]-=r['qty'];out.append(dict(r))
 return {'stock':s,'allocations':out}

def intervals(data):
 a=[list(x) for x in data['intervals']]
 if any(len(x)!=2 or any(type(v)is not int for v in x) or x[0]>x[1] for x in a):raise ValueError('interval')
 o=[]
 for x in sorted(a):
  if o and x[0]<=o[-1][1]:o[-1][1]=max(o[-1][1],x[1])
  else:o.append(x)
 return {'intervals':o}

SPECS=[
 {'key':'dependency_waves','prompts':["solve(data): nodes:string->string[], requested:string[], batch_size:positive int; output order and batches. Expand only transitive closure, dependencies first, stable requested order, each batch <= size.","Unknown node, cycle, malformed size raise ValueError whose cycle message names nodes; input immutable.","Test empty requested and diamond dependencies; implement pure function only."],'oracle':"from solution import solve\nimport copy\nd={'nodes':{'api':['db','cache'],'worker':['db'],'db':[],'cache':['db']},'requested':['api','worker'],'batch_size':2};b=copy.deepcopy(d);assert solve(d)=={'order':['db','cache','api','worker'],'batches':[['db','cache'],['api','worker']]} and d==b\nassert solve({'nodes':{},'requested':[],'batch_size':1})=={'order':[],'batches':[]}\nfor x in ({'nodes':{'a':['z']},'requested':['a'],'batch_size':1},{'nodes':{'a':[]},'requested':['a'],'batch_size':0},{'nodes':{'a':['b'],'b':['a']},'requested':['a'],'batch_size':2}):\n try:solve(x);raise AssertionError()\n except ValueError:pass",'reference':'dependency'},
 {'key':'event_replay','prompts':["solve(data): initial object and events with seq,event_id,op=set|delete,key,value; output state,last_seq,event_ids. Sort by seq and apply contiguous events.","Identical duplicate event_id is idempotent; conflicting id or seq, gap, bad op/seq raises ValueError; delete missing is allowed.","Input and initial remain unchanged; test reordered, duplicate, conflict and empty streams."],'oracle':"from solution import solve\nimport copy\nd={'initial':{'a':0},'events':[{'seq':2,'event_id':'b','op':'set','key':'a','value':2},{'seq':1,'event_id':'a','op':'set','key':'a','value':1}]};b=copy.deepcopy(d);assert solve(d)['state']=={'a':2} and d==b\nassert solve({'initial':{},'events':[]})['last_seq']==0\nfor e in ([{'seq':2,'event_id':'x','op':'set','key':'a','value':1}],[{'seq':1,'event_id':'x','op':'bad','key':'a','value':1}],[{'seq':1,'event_id':'x','op':'set','key':'a','value':1},{'seq':1,'event_id':'y','op':'delete','key':'a'}],[{'seq':1,'event_id':'x','op':'set','key':'a','value':1},{'seq':1,'event_id':'x','op':'set','key':'a','value':2}]):\n try:solve({'initial':{},'events':e});raise AssertionError()\n except ValueError:pass",'reference':'events'},
 {'key':'integer_split','prompts':["solve(data): amounts nonnegative integer cents[], shares.participants [{id,weight}]; output total_cents and id allocations. Use proportional floors then give remainder cents by largest fractional remainder, ties input order.","Weights are positive integers, total must be conserved; empty amounts/participants, negative or noninteger values raise ValueError.","Pure function, preserve input, support zero total and duplicate-free participant ids."],'oracle':"from solution import solve\nimport copy\nd={'amounts':[101],'shares':{'participants':[{'id':'a','weight':1},{'id':'b','weight':2}]}};b=copy.deepcopy(d);assert solve(d)=={'total_cents':101,'allocations':{'a':34,'b':67}} and d==b\nassert sum(solve({'amounts':[0],'shares':{'participants':[{'id':'a','weight':1}]}})['allocations'].values())==0\nfor d in ({'amounts':[],'shares':{'participants':[{'id':'a','weight':1}]}},{'amounts':[-1],'shares':{'participants':[{'id':'a','weight':1}]}},{'amounts':[1],'shares':{'participants':[{'id':'a','weight':0}]}}):\n try:solve(d);raise AssertionError()\n except ValueError:pass",'reference':'split'}]

SPECS += [
 {'key':'deep_migration','prompts':["solve(data): schema=1 nested JSON; recursively rename name->display_name and email->contact, null/missing contact becomes unknown; output schema=2.","Recurse through objects and arrays, preserve every undeclared field, array order and metadata; non schema 1 raises ValueError.","Pure and JSON serializable; input unchanged."],'oracle':"from solution import solve\nimport copy\nd={'schema':1,'users':[{'name':'A','email':None,'x':{'name':'N'}},{'profile':{'email':'b@x'}}]};b=copy.deepcopy(d);r=solve(d);assert d==b and r['schema']==2 and r['users'][0]['contact']=='unknown' and r['users'][1]['profile']['contact']=='b@x' and r['users'][0]['x']['display_name']=='N'\nfor x in ({'schema':2},{'schema':0},{'x':1}):\n try:solve(x);raise AssertionError()\n except ValueError:pass",'reference':'migrate'},
 {'key':'inventory_reserve','prompts':["solve(data): stock sku->nonnegative int and requests [{request_id,sku,qty positive int}]; output new stock and allocations in request order.","Validate the complete batch before decrementing; unknown SKU, insufficient stock, duplicate id or invalid qty raises ValueError and input is untouched.","Empty requests succeed; return new objects and no partial reservation."],'oracle':"from solution import solve\nimport copy\nd={'stock':{'A':3},'requests':[{'request_id':'r','sku':'A','qty':2}]};b=copy.deepcopy(d);assert solve(d)=={'stock':{'A':1},'allocations':d['requests']} and d==b\nassert solve({'stock':{'A':1},'requests':[]})['stock']=={'A':1}\nfor q in ([{'request_id':'x','sku':'Z','qty':1}],[{'request_id':'x','sku':'A','qty':2}],[{'request_id':'x','sku':'A','qty':1},{'request_id':'x','sku':'A','qty':1}]):\n try:solve({'stock':{'A':1},'requests':q});raise AssertionError()\n except ValueError:pass",'reference':'reserve'},
 {'key':'interval_union','prompts':["solve(data): intervals [[start,end]] integer coordinates; sort and merge overlapping or adjacent ranges; output intervals ascending.","Support empty, negative, duplicate and nested ranges; reject malformed length, noninteger endpoint or start>end with ValueError.","Pure function preserves input and validates all rows before returning."],'oracle':"from solution import solve\nimport copy\nd={'intervals':[[5,7],[1,3],[3,5],[-2,0]]};b=copy.deepcopy(d);assert solve(d)=={'intervals':[[-2,0],[1,7]]} and d==b\nassert solve({'intervals':[]})=={'intervals':[]}\nfor x in ([[1]],[[2,1]],[[1,'x']]):\n try:solve({'intervals':x});raise AssertionError()\n except ValueError:pass",'reference':'intervals'}]

def run_reference(s):
    p=Path(__file__).resolve(); code='from hard_delivery_contracts_v1 import '+s['reference']+' as solve\n'
    temp_root=Path('/tmp'); temp_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(temp_root)) as d:
        Path(d,'solution.py').write_text(code,encoding='utf8');Path(d,'oracle.py').write_text(s['oracle'],encoding='utf8')
        env=dict(__import__('os').environ); env['PYTHONPATH']=str(p.parent)
        return subprocess.run([sys.executable,'oracle.py'],cwd=d,capture_output=True,text=True,env=env)

if __name__=='__main__':
    for s in SPECS:
        r=run_reference(s);print(s['key'], 'PASS' if r.returncode==0 else 'FAIL', r.stderr.strip())
