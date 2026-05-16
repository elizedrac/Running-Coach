from services.llm import call_llm

END_WORDS = ["bye", "thanks", "that's all", "no thanks", "no", "nothing else", "that's it", "see you", "talk to you later"]
             
def is_end_message(query):
    cleaned = query.strip().lower()
    if any(word in cleaned for word in END_WORDS):
        return True
    return False

def detect_end(query, recent):
    if is_end_message(query):
        return True
    if "yes" in llm_response.lower():
        return True
    
    return False
    