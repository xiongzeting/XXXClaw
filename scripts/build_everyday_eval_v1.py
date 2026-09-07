"""Build a separate exposed development suite and calibrate its frozen oracles."""
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from MiniClaw.evaluation.oracle import evaluate_oracle
from MiniClaw.evaluation.models import load_eval_suite

FAMILIES = {
 'csv_cli': {
  'prompt':'修复 reader.py 和 cli.py：read_names(text) 用 CSV 规则读取 name 列，支持带逗号的引号字段和 Unicode。命令行从 stdin 读取，成功仅在 stdout 输出 JSON 字符串数组；缺少 name 表头时退出码 2，错误写 stderr，stdout 为空。保留函数接口。',
  'requirements':{'quoted_unicode':'CSV quoting and Unicode names','cli_json':'stdin to JSON stdout','invalid_header':'missing name: exit 2, stderr only'},
  'good':{'reader.py':"import csv,io\ndef read_names(text):\n r=csv.DictReader(io.StringIO(text))\n if 'name' not in (r.fieldnames or []): raise ValueError('missing name')\n return [row['name'] for row in r]\n",
          'cli.py':"import sys,json\nfrom reader import read_names\ntry: print(json.dumps(read_names(sys.stdin.read()),ensure_ascii=False))\nexcept ValueError as e:\n print(str(e),file=sys.stderr)\n sys.exit(2)\n"},
  'bad': [('reader.py', "def read_names(text):\n return [s.split(',')[0] for s in text.splitlines()[1:]]\n"),
          ('cli.py', "import sys,json\nfrom reader import read_names\nprint('DEBUG')\nprint(json.dumps(read_names(sys.stdin.read())))\n")]},
 'config_merge': {
  'prompt':'修复 settings.resolve(defaults, file_config, env)。优先级 env > file_config > defaults，不修改入参；APP_DEBUG 支持大小写无关 true/false，结果为 bool；APP_PORT 转成 1..65535 的 int；空环境字符串视为未设置；非法环境值抛 ValueError。保持 config_values.py 中的解析函数接口，修复跨文件调用。',
  'requirements':{'precedence_types':'precedence, typed values and no input mutation','empty_fallback':'empty environment falls back','reject_invalid':'invalid booleans and ports rejected'},
  'good':{'config_values.py':"def debug(value):\n value=value.lower()\n if value not in ('true','false'): raise ValueError('debug')\n return value=='true'\ndef port(value):\n n=int(value)\n if not 1<=n<=65535: raise ValueError('port')\n return n\n",
          'settings.py':"from config_values import debug,port\ndef resolve(defaults,file_config,env):\n out={**defaults,**file_config}\n for key,parse in [('debug',debug),('port',port)]:\n  value=env.get('APP_'+key.upper())\n  if value: out[key]=parse(value)\n return out\n"},
  'bad':[('config_values.py',"def debug(value): return bool(value)\ndef port(value): return int(value)\n"),
         ('settings.py',"def resolve(defaults,file_config,env):\n defaults.update(file_config)\n return defaults\n")]},
 'filtered_page': {
  'prompt':'修复 service.list_items(rows, active=None, offset=0, limit=20)：先按 active 过滤（None 不过滤），按 id 字符串升序，再分页；total 是过滤后的总数。返回 {total, items}，不得改变 rows 的顺序或内容。offset/limit 为非负整数，负数抛 ValueError；limit=0 返回空 items；越界页也为空。pagination.py 负责分页边界，修复两文件协作。',
  'requirements':{'filter_sort_page':'filter then sort then paginate, no mutation','bounds_total':'total before pagination and empty pages','reject_negative':'negative page arguments rejected'},
  'good':{'pagination.py':"def page(rows,offset,limit):\n if offset<0 or limit<0: raise ValueError('negative page')\n return rows[offset:offset+limit]\n",
          'service.py':"from pagination import page\ndef list_items(rows,active=None,offset=0,limit=20):\n selected=sorted([r for r in rows if active is None or r['active']==active],key=lambda r:r['id'])\n return {'total':len(selected),'items':page(selected,offset,limit)}\n"},
  'bad':[('pagination.py',"def page(rows,offset,limit): return rows[offset:offset+limit]\n"),
         ('service.py',"from pagination import page\ndef list_items(rows,active=None,offset=0,limit=20):\n selected=page(rows,offset,limit)\n return {'total':len(selected),'items':selected}\n")]}
}


def main():
    oracle = ROOT/'evals/oracles/everyday_development_v1.py'
    digest = hashlib.sha256(oracle.read_bytes()).hexdigest()
    cases = []; checks = []
    for name,family in FAMILIES.items():
        options = {'script':str(oracle),'sha256':digest,'entry':name,'requirements':family['requirements']}
        with tempfile.TemporaryDirectory(prefix='everyday-calibrate-') as temp:
            workspace = Path(temp)
            def write(files):
                for path,text in files.items(): (workspace/path).write_text(text,encoding='utf-8')
            write(family['good'])
            result = evaluate_oracle(options,workspace)
            assert result['classification']=='passed', result
            checks.append({'family':name,'variant':'reference','classification':result['classification']})
            for index,(path,bad) in enumerate(family['bad']):
                write(family['good']); (workspace/path).write_text(bad,encoding='utf-8')
                result = evaluate_oracle(options,workspace)
                assert result['classification']=='capability_failure',result
                checks.append({'family':name,'variant':'mutation-'+str(index),'classification':result['classification']})
        fixture = ROOT/'evals/fixtures/everyday-v1'/name; fixture.mkdir(parents=True,exist_ok=True)
        initial = dict(family['good']); initial.update(dict(family['bad']))
        for path,text in initial.items(): (fixture/path).write_text(text,encoding='utf-8')
        cases.append({'id':'everyday_v1_'+name,'category':'everyday-development',
            'fixture':'fixtures/everyday-v1/'+name,'phases':[{'id':'fix','prompt':family['prompt']}],
            'source':{'family':name,'split':'development','exposure':'exposed','difficulty':'everyday'},
            'checks':[{'type':'oracle','dimension':'outcome',**options}],
            'budgets':{'max_total_tokens':80000,'max_tool_calls':40},'timeout_seconds':600})
    suite = ROOT/'evals/development-everyday-v1.json'
    suite.write_text(json.dumps({'version':1,'name':'everyday-development-v1','cases':cases},ensure_ascii=False,indent=2),encoding='utf-8')
    assert len(load_eval_suite(suite).cases)==3
    report = ROOT/'.aster/evals/everyday-v1-calibration.json'
    report.write_text(json.dumps({'model_requests':0,'oracle_sha256':digest,'checks':checks},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'calibrations':len(checks),'model_requests':0,'suite':str(suite)}))


if __name__=='__main__': main()
