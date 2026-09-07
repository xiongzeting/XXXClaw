"""Independent everyday-development checks; never copied into the task workspace."""
import importlib
import json
import sys


def evaluate(family):
    sys.path.insert(0, '/workspace' if __import__('pathlib').Path('/workspace').exists() else __import__('os').getcwd())
    results = []
    def check(key, probe):
        try:
            passed = probe() is True
            evidence = 'independent behavior comparison: ' + str(passed)
        except Exception as exc:
            passed = False; evidence = type(exc).__name__ + ': ' + str(exc)[:300]
        results.append({'requirement_id':key,'passed':passed,'evidence':evidence})
    if family == 'csv_cli':
        def parsed():
            module = importlib.import_module('reader')
            return module.read_names('name,note\n"Smith, Jane","a,b"\n李雷,x\n') == ['Smith, Jane','李雷']
        def cli():
            import subprocess
            completed = subprocess.run([sys.executable,'cli.py'], input='name,note\nAda,x\n', text=True, capture_output=True)
            return completed.returncode == 0 and json.loads(completed.stdout) == ['Ada']
        def invalid():
            import subprocess
            completed = subprocess.run([sys.executable,'cli.py'], input='note\nx\n', text=True, capture_output=True)
            return completed.returncode == 2 and not completed.stdout.strip() and bool(completed.stderr.strip())
        check('quoted_unicode', parsed); check('cli_json', cli); check('invalid_header', invalid)
    elif family == 'config_merge':
        def precedence():
            m = importlib.import_module('settings'); defaults = {'debug':True,'port':8000}
            out = m.resolve(defaults, {'debug':True,'port':9000}, {'APP_DEBUG':'false','APP_PORT':'1234'})
            return out == {'debug':False,'port':1234} and defaults == {'debug':True,'port':8000}
        def empty():
            m = importlib.import_module('settings')
            return m.resolve({'debug':False,'port':8000},{},{'APP_DEBUG':'','APP_PORT':''}) == {'debug':False,'port':8000}
        def invalid():
            m = importlib.import_module('settings')
            for env in [{'APP_DEBUG':'maybe'},{'APP_PORT':'0'},{'APP_PORT':'65536'},{'APP_PORT':'abc'}]:
                try: m.resolve({'debug':False,'port':8000},{},env)
                except ValueError: continue
                return False
            return True
        check('precedence_types', precedence); check('empty_fallback', empty); check('reject_invalid', invalid)
    elif family == 'filtered_page':
        rows = [{'id':'b','active':True},{'id':'z','active':False},{'id':'a','active':True},{'id':'c','active':True}]
        def ordering():
            m = importlib.import_module('service'); original = [dict(r) for r in rows]
            return m.list_items(rows, active=True, offset=1, limit=1) == {'total':3,'items':[{'id':'b','active':True}]} and rows == original
        def bounds():
            m = importlib.import_module('service')
            return m.list_items(rows, active=False, offset=9, limit=2) == {'total':1,'items':[]} and m.list_items(rows, active=None, offset=0, limit=0) == {'total':4,'items':[]}
        def invalid():
            m = importlib.import_module('service')
            for kw in [{'offset':-1},{'limit':-1}]:
                try: m.list_items(rows, **kw)
                except ValueError: continue
                return False
            return True
        check('filter_sort_page', ordering); check('bounds_total', bounds); check('reject_negative', invalid)
    else:
        raise ValueError('unknown family')
    passed = all(c['passed'] for c in results)
    print(json.dumps({'protocol':'miniclaw-oracle-v1','status':'passed' if passed else 'capability_failure','checks':results}))
    return 0 if passed else 10


if __name__ == '__main__':
    try: code = evaluate(sys.argv[1])
    except Exception as exc:
        print(json.dumps({'protocol':'miniclaw-oracle-v1','status':'oracle_invalid','error':str(exc)})); code = 20
    sys.exit(code)
