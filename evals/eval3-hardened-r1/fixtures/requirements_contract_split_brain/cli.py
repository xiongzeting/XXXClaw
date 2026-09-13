import json,sys,os,tempfile
from pathlib import Path
from solution import solve
def main():
    if len(sys.argv)!=3: return 2
    try:
        data=json.loads(Path(sys.argv[1]).read_text('utf-8'))
        result=solve(data)
        # TODO: support atomic output and preserve old output on all failures.
        Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False),'utf-8')
        return 0
    except (ValueError,OSError,KeyError,TypeError): return 1
if __name__=='__main__': sys.exit(main())
