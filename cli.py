# CLI entry point (V1). Thin wrapper that calls services/coach.py::ask().
import os
from db import client
from dotenv import load_dotenv
from services.coach import orchestrate
from models.planner import History

load_dotenv()

USER_ID = os.getenv("USER_ID")
_hist = History()

def main():
    while True:
        user_input = input("You: ")
        if user_input.lower().strip() in ["exit", "quit", "q", "bye",""]:
            break

        response = orchestrate(user_input, USER_ID, _hist)[0]
        if "It seems like you want to end the conversation." in response:
            break
             
        print(f"Coach: {response}")
    

if __name__ == "__main__":
    main()