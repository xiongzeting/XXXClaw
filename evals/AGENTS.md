# Evaluation Instructions

## Network recovery

- ConnectError, ReadError, and similar interruptions are not automatically model failures.
- Retry only the interrupted model request while preserving prior messages, tool results, and workspace state.
- Do not replay writes or other side effects without checking their state first.
- Do not retry indefinitely or hide failures by increasing budgets.
- Report original failures, retry counts, recovery status, and final delivery separately.

## Exposure and scoring

- A task used for development or code changes is an exposed regression and must not be reported as an unseen score.
- Eval may save phase answers, workspace files, traces, and usage for later judging.
- Pending judge results remain pending; they are not passes.
- Result quality may be judged by a later LLM, while process, efficiency, safety, reliability, workspace state, and tool errors remain programmatically checked.

## Timing and usage

- Compare effective runtime after subtracting model-provider wait time when complete timing data is available.
- Report provider latency separately and preserve raw wall-clock duration.
- For parallel requests, subtract the union of model-wait intervals only once.
- Always report concurrency and distinguish measured values from estimates.

## Tool and artifact accounting

- Preserve real tool errors, permission protections, cancellations, retries, and execution budgets.
- Count context artifacts from both structured trace metadata and the delivered artifact marker.
- Distinguish live tool artifacts, historical tool archives, closed-history archives, soft compactions, and hard compactions.
