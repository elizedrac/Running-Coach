from services.llm import call_llm
from services.prompts import END_DETECTION, FOLLOW_UP
from models.planner import EndBehaviorClassification
import sys

END_WORDS = {"bye", "thanks", "thank you", "that's all", "no thanks", "nope", "all good", "ok"}
             
def is_end_message(query):
    cleaned = query.lower().strip().rstrip(".!?")
    if cleaned in END_WORDS:
        return True
    return any(word in cleaned for word in END_WORDS)

def detect_end(query, recent):
    if is_end_message(query):
        prompt = f"User current query: {query}, recent conversation: {recent}."
        response = call_llm(system_prompt=END_DETECTION, user_prompt=prompt, model="claude-haiku-4-5-20251001", cache_system=True)
        response = response.strip()
        start = response.find("{")
        end = response.rfind("}") + 1
        response = response[start:end]

        try:
            response = EndBehaviorClassification.model_validate_json(response)
            return response.end_conversation

        except Exception as e:
            if "--debug" in sys.argv:  
                print("Error parsing end behavior output:", e)
            return False

    return False

def generate_followups(query, recent):
    import json
    prompt = f"User current query: {query}, recent conversation: {recent}."
    response = call_llm(system_prompt=FOLLOW_UP, user_prompt=prompt, model="claude-haiku-4-5-20251001", cache_system=True).strip()
    start = response.find("{")
    end = response.rfind("}") + 1
    try:
        return json.loads(response[start:end]).get("follow_ups", [])
    except Exception as e:
        if "--debug" in sys.argv:
            print("Error parsing follow-ups:", e)
        return []
    