from __future__ import annotations

"""Minimal real-provider smoke test that never prints API keys."""

import argparse
import asyncio
import json
from pathlib import Path

from .config import load_llm_settings
from .env_file import merged_environment, read_env_file
from .factory import create_model_client
from .types import ChatMessage, ModelProfile, ModelRequest


async def run_smoke(provider: str | None = None, env_file: str | None = None) -> int:
    file_values = read_env_file(Path(env_file)) if env_file else {}
    environment = merged_environment(file_values)
    settings = load_llm_settings(
        provider=provider or file_values.get("MINICLAW_PROVIDER"),
        model_id=file_values.get("MINICLAW_PRIMARY_MODEL") or file_values.get("MINICLAW_MODEL"),
        base_url=file_values.get("MINICLAW_PRIMARY_BASE_URL") or file_values.get("MINICLAW_BASE_URL"),
        environment=environment,
    )
    client = create_model_client(settings)
    request = ModelRequest(
        profile=ModelProfile(model_id=settings.model_id, max_output_tokens=32),
        messages=[ChatMessage(role="user", content="Reply with exactly: MINICLAW_OK")],
    )
    final_reply = None
    async for event in client.stream(request):
        if event.type == "completed":
            final_reply = event.reply
    success = bool(final_reply and not final_reply.error and "MINICLAW_OK" in final_reply.content)
    output = {
        "provider": settings.provider,
        "model": settings.model_id,
        "base_url": settings.base_url,
        "success": success,
        "response": final_reply.content.strip() if final_reply and not final_reply.error else "",
        "error": final_reply.error if final_reply else "no completed reply",
    }
    print(json.dumps(output, ensure_ascii=False))
    return 0 if success else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Test the configured MiniClaw LLM provider")
    parser.add_argument("--provider", choices=["primary", "openai", "zxcoding", "deepseek"])
    parser.add_argument("--env-file")
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(run_smoke(arguments.provider, arguments.env_file)))


if __name__ == "__main__":
    main()
