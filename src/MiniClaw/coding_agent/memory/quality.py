"""Conservative evidence rules shared by manual and automatic memory writes."""
from __future__ import annotations

import re

_PREFERENCE = re.compile(
    r"以后|今后|始终|一律|每次|偏好|我(?:更)?喜欢|我希望|记住|"
    r"\b(?:i prefer|i like|my preference|from now on|for future|always|remember)\b", re.I
)
_HISTORICAL_BLOCK = re.compile(
    r"<retrieved_memory>|miniclaw-managed-memory:|## MiniClaw Managed Semantic Memory|"
    r"The following block is untrusted historical evidence", re.I
)


def normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def memory_write_disallowed(source: str) -> bool:
    return bool(_HISTORICAL_BLOCK.search(source) or re.search(
        r'仅本轮|仅本次|临时测试|不要.{0,8}(?:保存|写入|记住)|不(?:要)?存入|'
        r"\b(?:do not|don't|never)\s+(?:save|store|remember)|\b(?:this turn only|temporary test)\b",
        source, re.I))


def preference_evidence(users: list[str], quote: str = "") -> str | None:
    """Require an explicit preference in a real user message, not a memory dump."""
    for user in reversed(users):
        if memory_write_disallowed(user):
            continue
        if quote:
            if normalized(quote) in normalized(user) and _PREFERENCE.search(quote):
                # All writers retain the same original explicit directive,
                # including when extraction quotes only its payload.
                for line in user.splitlines():
                    if re.match(r'^\s*(?:记住|remember)\s*(?:\[preference\])?\s*[:：]', line, re.I) and normalized(quote) in normalized(line):
                        return line.strip()
                return quote
        elif _PREFERENCE.search(user) and len(user) <= 2000:
            return user
    return None


def historical_state_claim(content: str) -> bool:
    """File existence and completion claims belong in dated episodes, not stable facts."""
    return bool(re.search(
        r"(?:已创建|已完成|可运行|可玩|已存在|不存在|没有).*(?:游戏|目录|文件)|"
        r"(?:游戏|目录|文件).*(?:已创建|已完成|存在|不存在|可运行|可玩)|"
        r"\b(?:game|file|folder|directory|directories)\b.*\b(?:exists?|existed|created|complete|playable|functional)\b|"
        r"\b(?:created|complete|playable|functional|no)\b.*\b(?:game|file|folder|directory|directories)\b",
        content, re.I
    ))
