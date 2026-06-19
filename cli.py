# CLI entry point (V1). Thin wrapper that calls services/coach.py::ask().
import os

from dotenv import load_dotenv

from db import client
from models.planner import History
from services.coach import orchestrate
from services.end import detect_end

load_dotenv()

USER_ID = os.getenv("USER_ID")
_hist = History()


def main():
    while True:
        user_input = input("You: ")
        if user_input.lower().strip() in ["exit", "quit", "q", "bye", ""]:
            break

        if detect_end(user_input, _hist.recent):
            print(
                "It seems like you want to end the conversation. If that's the case, it was great chatting with you! If not, feel free to ask me anything else."
            )
            break

        response_parts = []
        for event_type, data in orchestrate(user_input, USER_ID, _hist):
            if event_type == "chunk":
                print(data, end="", flush=True)  # streams to terminal in real time
                response_parts.append(data)
            elif event_type == "status":
                print(f"\n[{data}]", flush=True)
            elif event_type == "done":
                pass
        print()


if __name__ == "__main__":
    main()
