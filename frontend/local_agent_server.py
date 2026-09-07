from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import sys
import threading
import uuid
import queue
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# Some Windows Conda environments load Intel OpenMP through both NumPy and a
# retrieval dependency. Set this before importing MiniClaw so the local demo
# can start; users who control a clean environment should prefer one OpenMP
# runtime instead.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from MiniClaw.coding_agent.approval import cli_approval_handler, load_approval_settings
from MiniClaw.coding_agent.assistant.coding import CodingAssistant
from MiniClaw.coding_agent.runtime import load_runtime_settings
from MiniClaw.llm.config import load_llm_settings
from MiniClaw.llm.env_file import merged_environment, read_env_file
from MiniClaw.llm.factory import create_model_client, model_profile_from_settings


class AgentSession:
    def __init__(self, workspace: Path, env_file: Path, provider: str | None, model: str | None,
                 sandbox: str | None, workspace_mode: str | None, approval_policy: str | None,
                 session_id: str | None = None):
        values = read_env_file(env_file) if env_file.is_file() else {}
        self.environment = merged_environment(values)
        # Browser-local agent runs often spend longer in tool planning and
        # large-context retrieval than the CLI defaults. Keep the UI patient:
        # total 30 minutes, first token 10 minutes, idle stream 5 minutes.
        self.environment.setdefault("MINICLAW_LLM_TOTAL_TIMEOUT", "1800")
        self.environment.setdefault("MINICLAW_LLM_FIRST_TOKEN_TIMEOUT", "600")
        self.environment.setdefault("MINICLAW_LLM_IDLE_TIMEOUT", "300")
        # Default to the original primary route and its configured model.
        # Explicit provider/model overrides retain their own provider defaults.
        settings = load_llm_settings(
            provider=provider or "primary",
            model_id=model,
            environment=self.environment,
        )
        runtime = load_runtime_settings(self.environment, sandbox=sandbox, workspace_mode=workspace_mode)
        approval = load_approval_settings(workspace, self.environment, policy=approval_policy)
        self.settings, self.runtime, self.approval = settings, runtime, approval
        self.workspace = workspace.resolve()
        self.session_id = uuid.UUID(session_id).hex if session_id else uuid.uuid4().hex
        self.assistant = CodingAssistant(
            model_client=create_model_client(settings),
            profile=model_profile_from_settings(settings),
            workspace=self.workspace,
            session_path=self.workspace / '.aster' / 'web' / self.session_id / 'session.jsonl',
            session_id=self.session_id,
            runtime_settings=runtime,
            approval_settings=approval,
            environment=self.environment,
            approval_handler=lambda request: cli_approval_handler(request, approval.timeout_seconds),
            trace_provider=settings.provider,
        )
        # Serialize HTTP submissions while the assistant keeps one event loop.
        self.lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.status = 'idle'
        self.last_result = None
        self._last_results = {self.session_id: None}
        self._init_args = (workspace, env_file, provider, model, sandbox, workspace_mode, approval_policy)
        self._assistants = {self.session_id: self.assistant}

    def reset(self):
        return self.select(None)

    def _activate(self, session_id: str | None):
        requested = uuid.UUID(session_id).hex if session_id else uuid.uuid4().hex
        session_path = self.workspace / '.aster' / 'web' / requested / 'session.jsonl'
        if session_id and requested not in self._assistants and not session_path.is_file():
            raise FileNotFoundError('该会话没有可恢复的服务端记录，请新建对话。')
        if requested not in self._assistants:
            self._assistants[requested] = CodingAssistant(
                model_client=create_model_client(self.settings),
                profile=model_profile_from_settings(self.settings),
                workspace=self.workspace, session_path=session_path, session_id=requested,
                runtime_settings=self.runtime, approval_settings=self.approval,
                environment=self.environment,
                approval_handler=lambda request: cli_approval_handler(request, self.approval.timeout_seconds),
                trace_provider=self.settings.provider,
            )
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.touch(exist_ok=True)
        self.session_id = requested
        self.assistant = self._assistants[requested]
        if not hasattr(self, '_last_results'):
            self._last_results = {}
        self._last_results.setdefault(requested, None)

    def select(self, session_id: str | None):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('当前任务仍在运行，请先停止任务。')
        try:
            self._activate(session_id)
            return self.info()
        finally:
            self.lock.release()

    def history(self):
        path = self.workspace / '.aster' / 'web' / self.session_id / 'session.jsonl'
        messages, calls = [], {}
        if not path.exists():
            return {'session_id': self.session_id, 'messages': messages}
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                message = json.loads(line).get('message')
            except json.JSONDecodeError:
                continue
            if not message:
                continue
            if message.get('name') == 'miniclaw_context' and str(message.get('content','')).startswith('[MiniClaw Context Update]\n'):
                continue
            role, content = message.get('role'), message.get('content', '')
            if role in ('user', 'assistant') and content:
                messages.append({'kind': role, 'text': content,
                                 'phase': 'progress' if message.get('tool_calls') else ''})
            for call in message.get('tool_calls') or []:
                calls[call['call_id']] = call
            if role == 'tool':
                call_id = message.get('tool_call_id')
                call = calls.get(call_id, {})
                # The persisted message body is not an authoritative exit code.
                # Resolve state from the recorded tool event below when available.
                messages.append({'kind': 'tool', 'id': call_id, 'name': message.get('name'),
                                 'arguments': call.get('arguments', {}), 'state': 'unknown',
                                 'result': content})
        trace = path.with_name('trace.jsonl')
        statuses = {}
        if trace.exists():
            for line in trace.read_text(encoding='utf-8').splitlines():
                try:
                    row = json.loads(line)
                    if row.get('type') == 'tool.call':
                        data = row.get('data', {})
                        statuses[data.get('tool_call_id')] = data.get('status')
                except json.JSONDecodeError:
                    continue
        for message in messages:
            if message['kind'] == 'tool':
                message['state'] = {'success': 'done', 'error': 'error', 'cancelled': 'interrupted',
                                    'blocked': 'error'}.get(statuses.get(message['id']), 'unknown')
        return {'session_id': self.session_id, 'messages': messages}

    async def run(self, prompt: str) -> dict:
        events = []
        with self.lock:
            async for event in self.assistant.run(prompt):
                row = {"type": event.type}
                for key in ("text", "text_delta", "content", "tool_name", "tool_call_id", "error", "run_id"):
                    value = getattr(event, key, None)
                    if value is not None:
                        row[key] = value
                events.append(row)
        text_parts = []
        for event in events:
            text = event.get("text_delta") or event.get("text") or event.get("content")
            if isinstance(text, str):
                text_parts.append(text)
        return {"events": events, "text": "".join(text_parts), "goal": self.assistant.goal_status()}

    async def stream(self, prompt: str, output: queue.Queue, resume: bool = False):
        terminal = None
        try:
            self.status = '正在检索上下文并连接模型'
            output.put({'event': 'status', 'data': {'text': self.status}})
            events = self.assistant.resume() if resume else self.assistant.run(prompt)
            async for event in events:
                row = {"type": event.type, 'text': event.text, 'is_error': event.is_error,
                       'cancelled': bool((event.details or {}).get('cancelled'))}
                if event.details:
                    row['details'] = event.details
                if event.tool_call:
                    row.update(tool_name=event.tool_call.name, tool_call_id=event.tool_call.call_id,
                               arguments=event.tool_call.arguments)
                if event.tool_result is not None:
                    row['tool_result'] = event.tool_result
                if event.type == 'turn_started':
                    self.status = '等待模型响应'
                elif event.type == 'text_delta':
                    self.status = '正在生成回复'
                elif event.type == 'tool_started':
                    self.status = '正在执行工具：' + row.get('tool_name', '')
                elif event.type == 'tool_finished':
                    self.status = '工具已返回，继续处理'
                elif event.type == 'turn_finished':
                    self.status = '正在整理会话记录'
                elif event.type == 'run_finished':
                    details = event.details or {}
                    stop_reason = details.get('stop_reason')
                    terminal = {
                        'status': (
                            'error' if event.is_error or stop_reason == 'error' else
                            'cancelled' if stop_reason == 'aborted' else
                            'paused' if stop_reason == 'budget_exhausted' or details.get('status') == 'paused' else
                            'success'
                        ),
                        'stop_reason': stop_reason,
                        'error': details.get('error') if event.is_error else None,
                        'resumable': bool(details.get('resumable')),
                    }
                    progress = getattr(self.assistant, 'task_progress', None)
                    state = getattr(progress, 'state', {}) if progress else {}
                    terminal.update({
                        'next_action': state.get('next_action'),
                        'pending': list(getattr(progress, 'pending', lambda: [])()),
                        'remaining': state.get('remaining', []),
                        'trace_id': details.get('trace_id'),
                        'run_id': details.get('run_id'),
                    })
                    row.update(terminal)
                    self.status = {'success': '任务已完成', 'paused': '任务已暂停',
                                   'cancelled': '任务已取消', 'error': '任务失败'}[terminal['status']]
                output.put({"event": "agent", "data": row})
            terminal = terminal or {'status': 'error', 'stop_reason': 'missing_terminal_event',
                                    'error': '任务结束但未收到真实运行状态', 'resumable': False,
                                    'next_action': None, 'pending': [], 'remaining': []}
            self.last_result = terminal
            if not hasattr(self, '_last_results'):
                self._last_results = {}
            if hasattr(self, 'session_id'):
                self._last_results[self.session_id] = terminal
            output.put({"event": "done", "data": {**terminal, 'goal': self.assistant.goal_status()}})
        except BaseException as exc:
            terminal = {'status': 'error', 'stop_reason': 'error', 'error': f'{type(exc).__name__}: {exc}',
                        'resumable': False, 'next_action': None, 'pending': [], 'remaining': []}
            self.last_result = terminal
            if not hasattr(self, '_last_results'):
                self._last_results = {}
            if hasattr(self, 'session_id'):
                self._last_results[self.session_id] = terminal
            output.put({'event': 'error', 'data': {**terminal}})
        finally:
            self.status = 'idle'
            self.lock.release()

    def submit(self, prompt, session_id=None, resume=False):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('当前已有任务运行中，请等待完成或点击停止。')
        try:
            if session_id:
                self._activate(session_id)
            output = queue.Queue()
            self.status = '正在连接模型'
            asyncio.run_coroutine_threadsafe(self.stream(prompt, output, resume=resume), self.loop)
            return output
        except BaseException:
            self.lock.release()
            raise

    def info(self) -> dict:
        progress = getattr(self.assistant, 'task_progress', None)
        progress_state = getattr(progress, 'state', {}) if progress else {}
        last_result = getattr(self, '_last_results', {}).get(self.session_id)
        if progress_state.get('status') == 'paused':
            last_result = {
                **(last_result or {}), 'status': 'paused', 'resumable': True,
                'next_action': progress_state.get('next_action'),
                'pending': list(getattr(progress, 'pending', lambda: [])()),
                'remaining': progress_state.get('remaining', []),
            }
        return {
            'version': 'web-stream-v4',
            'session_id': self.session_id,
            'status': self.status,
            "workspace": str(self.workspace),
            "provider": self.settings.provider,
            "model": self.settings.model_id,
            "base_url": self.settings.base_url,
            "sandbox": self.runtime.sandbox,
            "workspace_mode": self.runtime.workspace_mode,
            "approval": self.approval.policy,
            "tools": [item["name"] for item in self.assistant.tool_executor.definitions()],
            "goal": self.assistant.goal_status(),
            "last_result": last_result,
        }


SESSION: AgentSession | None = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = "MiniClawLocalUI/1.0"

    def _send(self, status: int, payload, content_type="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else (
            json.dumps(payload, ensure_ascii=False).encode("utf-8") if content_type.startswith("application/json") else str(payload).encode("utf-8")
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/info":
            return self._send(200, SESSION.info() if SESSION else {"error": "not initialized"})
        if path == '/api/history':
            return self._send(200, SESSION.history())
        if path in ("/", "/index.html"):
            data = (ROOT / "local_agent.html").read_bytes()
            return self._send(200, data, "text/html; charset=utf-8")
        asset = (ROOT / path.lstrip("/")).resolve()
        if asset.parent == ROOT and asset.is_file():
            mime = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
            return self._send(200, asset.read_bytes(), mime)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/cancel':
            SESSION.loop.call_soon_threadsafe(SESSION.assistant.cancel, 'Stopped from browser')
            return self._send(200, {'ok': True})
        if path in ("/api/new", "/api/session"):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
                if path == '/api/session' and not body.get('session_id'):
                    raise ValueError('session_id is required')
                info = SESSION.reset() if path == '/api/new' else SESSION.select(body['session_id'])
                return self._send(200, {"ok": True, **info})
            except RuntimeError as exc:
                return self._send(409, {'error': str(exc)})
            except FileNotFoundError as exc:
                return self._send(404, {'error': str(exc)})
            except (ValueError, TypeError) as exc:
                return self._send(400, {'error': str(exc)})
        if path not in ("/api/chat", "/api/chat/stream", "/api/resume"):
            return self._send(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
            resume = path == '/api/resume' or body.get('resume') is True
            prompt = str(body.get("message", "")).strip()
            if not prompt and not resume:
                return self._send(400, {"error": "message is required"})
            try:
                events = SESSION.submit(prompt, body.get('session_id'), resume=resume)
            except RuntimeError as exc:
                return self._send(409, {'error': str(exc)})
            if path == '/api/chat':
                rows = []
                while True:
                    item = events.get()
                    if item['event'] == 'agent':
                        rows.append(item['data'])
                    if item['event'] in ('done', 'error'):
                        return self._send(200, {'events': rows, 'text': ''.join(e['text'] for e in rows if e['type'] == 'text_delta'), **item['data']})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            started = time.monotonic()
            while True:
                try:
                    item = events.get(timeout=2)
                except queue.Empty:
                    item = {'event': 'status', 'data': {'text': SESSION.status, 'elapsed': round(time.monotonic()-started)}}
                payload = f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n".encode("utf-8")
                self.wfile.write(f"{len(payload):X}\r\n".encode("ascii") + payload + b"\r\n")
                self.wfile.flush()
                if item["event"] in ("done", "error"):
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                    break
        except Exception as exc:
            if path == "/api/chat/stream":
                try:
                    payload = f"event: error\ndata: {json.dumps({'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False)}\n\n".encode("utf-8")
                    self.wfile.write(f"{len(payload):X}\r\n".encode("ascii") + payload + b"\r\n0\r\n\r\n")
                    self.wfile.flush()
                except Exception:
                    pass
            else:
                self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt, *args):
        print(f"[local-ui] {self.address_string()} - {fmt % args}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Run a local MiniClaw browser UI")
    parser.add_argument("--workspace", default=str(PROJECT_ROOT))
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--session-id", help="Resume an existing web session UUID after restart")
    # The repository's general CLI may default to Docker, but the local browser
    # demo should start without requiring a pre-built image. Docker remains an
    # explicit opt-in via --sandbox docker.
    parser.add_argument("--sandbox", choices=["host", "docker"], default="host")
    parser.add_argument("--workspace-mode", choices=["direct", "snapshot"])
    # A browser has no stdin prompt. Default to allow so tool calls do not hang
    # waiting for the CLI approval handler; use --approval-policy ask only when
    # running the server from an interactive console that can answer prompts.
    parser.add_argument("--approval-policy", choices=["allow", "ask", "deny"], default="allow")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    global SESSION
    try:
        SESSION = AgentSession(Path(args.workspace), Path(args.env_file), args.provider, args.model,
                               args.sandbox, args.workspace_mode, args.approval_policy, args.session_id)
    except Exception as exc:
        raise SystemExit(f"MiniClaw 初始化失败: {type(exc).__name__}: {exc}") from exc
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"MiniClaw Local UI: http://{args.host}:{args.port}")
    # Windows consoles may use cp932/GBK and fail on workspace names or goal
    # text containing Chinese. The browser is the real consumer of this data.
    print(json.dumps(SESSION.info(), ensure_ascii=True, indent=2))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
