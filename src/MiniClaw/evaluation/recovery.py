"""Local network recovery: never rerun a whole suite or replay successful tools."""
import asyncio
from MiniClaw.llm.recovery import is_network_error


async def with_network_recovery(assistant, events, retries=5):
    """Recover only the interrupted model request.

    The counter is consecutive-failure scoped. A completed tool call/result
    pair proves the agent made progress and starts the next outage window at 0.
    """
    consecutive_retries = 0
    while True:
        async for event in events:
            if (
                event.type == "tool_finished"
                and not event.is_error
                and not (event.details or {}).get("cancelled")
            ):
                consecutive_retries = 0
            yield event
        state = assistant.run_state_store.read()
        if (consecutive_retries >= retries or state is None or state.status != "failed"
                or not is_network_error({"error": state.error})
                or assistant.loop.pending_request is None):
            return
        await asyncio.sleep(min(32.0, 2 ** consecutive_retries))
        consecutive_retries += 1
        events = assistant.retry_network()


def recovered_run_ids(records):
    parents = {}
    completed = {}
    for record in records:
        data = record.get("data") or {}
        run_id = record.get("run_id")
        if record.get("type") == "run.started" and data.get("network_recovery_of"):
            parents[run_id] = data["network_recovery_of"]
        elif record.get("type") == "run.completed":
            completed[run_id] = data
    recovered = set()
    for run_id, data in completed.items():
        if data.get("status") != "success":
            continue
        seen = set()
        while run_id in parents and run_id not in seen:
            seen.add(run_id)
            run_id = parents[run_id]
            prior = completed.get(run_id, {})
            if prior.get("status") != "error" or not is_network_error(prior):
                break
            recovered.add(run_id)
    return recovered
