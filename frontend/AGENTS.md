# Frontend Instructions

## Local server

Run from the project root:

```powershell
python frontend/local_agent_server.py `
  --workspace D:\MIniClaw `
  --provider deepseek `
  --model deepseek-chat `
  --port 8767
```

The server reads provider credentials from the project environment. Never
put API keys in frontend source, browser storage, traces, or replies.

## Runtime behavior

- The browser UI uses the real `CodingAssistant`, tools, memory, artifacts, and runtime.
- Host and Docker runtimes may have different shell syntax; the bash tool description must state the active environment.
- Tool results represented as context artifacts show a path, size, hash, and short summary; the summary is not the complete result. Reading the artifact file must not create a second artifact.
- Keep artifact files readable from the model's actual workspace when the task may need full recovery.

## Sessions

- Web sessions are stored under `.aster/web/<session-id>/`.
- The most recent session is recorded in `.aster/web/last-session-id` and is resumed on restart when available.
- Creating a new conversation updates the most-recent session.
- Conversation deletion requires explicit user confirmation.

## UI changes

- Keep the conversation list, server session state, and browser local state consistent.
- Changes to conversation deletion or message editing must update the server session, not only the visible browser list.
- Run JavaScript syntax checks and focused server/frontend tests after changes.
