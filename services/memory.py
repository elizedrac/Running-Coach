from services.llm import call_llm
from services.logging_config import get_logger
from services.prompts import COMPRESSION

logger = get_logger(__name__)


def compress_history(history: str) -> str:
    prompt = f"The user's current compressed history + 5 most recent turns: {history}"

    response = call_llm(
        system_prompt=COMPRESSION,
        user_prompt=prompt,
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        cache_system=True,
    )

    if response:
        return response
    logger.warning("memory_compression_empty", extra={"history_chars": len(history)})
    return history
