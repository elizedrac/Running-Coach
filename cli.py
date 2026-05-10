# CLI entry point (V1). Thin wrapper that calls services/coach.py::ask().
import os
from dotenv import load_dotenv
from services.planner import planner


load_dotenv()

USER_ID = os.getenv("USER_ID")

def main():
    while True:
        user_input = input("You: ")
        if user_input.lower().strip() in ["exit", "quit", "q", "bye",""]:
            break

        print(f"Coach: {planner(user_input)}")

if __name__ == "__main__":
    main()