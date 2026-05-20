from typing import Tuple

def input_check(query: str) -> Tuple[bool, str]:
    if len(query.strip()) < 2:
        return True, "Looks like you got cut off. Want to resend your message?"
    if len(query.split()) > 150:
        return True, "That message is a bit long — want to break it into smaller questions?"    
    return False, ""
