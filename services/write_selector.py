# Haiku call that picks a write action + args from REGISTRY (write path — see sql_selector.py for reads).
import json
import sys

from db.user_info import set_theme
from models.planner import WritePlan
from services.llm import call_llm
from services.prompts import WRITE_SELECTOR_SYSTEM

VALID_THEMES = {"arc", "jarvis", "hotrod", "stealth", "workshop"}

REGISTRY = {
    "set_theme": {
        "description": "Change the app's color theme.",
        "args": {"theme": "one of: arc, jarvis, hotrod, stealth, workshop"},
    },
}


def select_write(action_intent: str) -> str:
    USER_QUERY = f"""User intent: {action_intent}
Available actions: {list(REGISTRY.keys())}
Action descriptions: {REGISTRY}"""

    response = call_llm(
        system_prompt=WRITE_SELECTOR_SYSTEM,
        user_prompt=USER_QUERY,
        model="claude-haiku-4-5-20251001",
        cache_system=True,
    )

    response = response.strip()
    start = response.find("{")
    end = response.rfind("}") + 1
    return response[start:end]


def execute_write(user_id, action_intent: str) -> dict:
    raw = select_write(action_intent)
    response = WritePlan.model_validate(json.loads(raw))

    if "--debug" in sys.argv:
        print(f"[write_selector] picked: {response.action} args={response.args}", file=sys.stderr)

    if response.action == "set_theme":
        theme = response.args.get("theme")
        if theme not in VALID_THEMES:
            return {"status": "clarify", "action": "set_theme", "options": sorted(VALID_THEMES)}
        return {"status": "success", "action": "set_theme", "theme": theme, "data": set_theme(user_id, theme)}

    return {"status": "error", "reason": "could not determine what setting to change"}
