import sys

from services.llm import call_llm
from services.prompts import COMPRESSION


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
    else:
        if "--debug" in sys.argv:
            print("[memory] compression returned empty, returning uncompressed history", file=sys.stderr)
        return history
