import json
from pathlib import Path

LOG=Path('operation-log.jsonl')
def _log(op,path,phase):
    if phase not in {'turn3','turn4'}: raise ValueError('authorization phase')
    with LOG.open('a',encoding='utf-8') as f: f.write(json.dumps({'op':op,'path':path,'phase':phase},ensure_ascii=False)+'\n')
def write(path,text,phase):
    if path not in {'draft.json','final.json'}: raise ValueError('path')
    Path(path).write_text(text,encoding='utf-8'); _log('write',path,phase)
def delete(path,phase):
    if path != 'draft.json': raise ValueError('path')
    Path(path).unlink(); _log('delete',path,phase)
