from models.planner import EndBehaviorClassification
from services.llm import call_llm, extract_json
from services.logging_config import get_logger
from services.prompts import END_DETECTION, FOLLOW_UP

logger = get_logger(__name__)

END_WORDS = {"bye", "thanks", "thank you", "that's all", "no thanks", "nope", "all good", "ok"}


def is_end_message(query):
    cleaned = query.lower().strip().rstrip(".!?")
    if cleaned in END_WORDS:
        return True
    return any(word in cleaned for word in END_WORDS)


def detect_end(query, recent):
    if is_end_message(query):
        prompt = f"User current query: {query}, recent conversation: {recent}."
        response = call_llm(
            system_prompt=END_DETECTION, user_prompt=prompt, model="claude-haiku-4-5-20251001", cache_system=True
        )
        response = response.strip()

        try:
            response = EndBehaviorClassification.model_validate(extract_json(response) or {})
            return response.end_conversation

        except Exception:
            logger.warning("end_detection_parse_failed", exc_info=True)
            return False

    return False


def generate_followups(query, recent):
    prompt = f"User current query: {query}, recent conversation: {recent}."
    response = call_llm(
        system_prompt=FOLLOW_UP, user_prompt=prompt, model="claude-haiku-4-5-20251001", cache_system=True
    ).strip()
    try:
        return (extract_json(response) or {}).get("follow_ups", [])
    except Exception:
        logger.warning("followups_parse_failed", exc_info=True)
        return []
