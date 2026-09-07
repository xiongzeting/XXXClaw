"""Internal execution checkpoint and ungraded final-answer storage. No model tool."""
import json
import os
from pathlib import Path


class SessionDelivery:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory/'execution-checkpoint.json'
        self.state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        temp=self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state,ensure_ascii=False),encoding='utf-8')
        os.replace(temp,self.path)

    def begin_user_turn(self, prompt):
        self.state={'status':'active','prompt':prompt}
        self.save()

    def finish(self, reason, messages):
        self.state.update(status='paused' if reason=='budget_exhausted' else
                          'interrupted' if reason in {'error','aborted'} else 'submitted',
                          stop_reason=reason, message_count=len(messages))
        self.save()
        return reason

    def submit(self, run_id, final_text, status, stop_reason, error, trace_path):
        folder=self.directory/'submissions';folder.mkdir(parents=True,exist_ok=True)
        value={'run_id':run_id,'final_answer':final_text,'execution_status':status,
               'stop_reason':stop_reason,'error':error,'judge_status':'pending',
               'trace_path':str(trace_path),'session_path':str(self.directory/'session.jsonl')}
        path=folder/(run_id+'.json')
        if path.exists():raise FileExistsError('Submission already saved: '+run_id)
        path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        return path
