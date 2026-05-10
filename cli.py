# CLI entry point (V1). Thin wrapper that calls services/coach.py::ask().
import os
from dotenv import load_dotenv

load_dotenv()

USER_ID = os.getenv("USER_ID")

def main():
    while True:
        user_input = input("You: ")
        if user_input.lower().strip() in ["exit", "quit", "q", "bye",""]:
            break

        print("Coach not implemnetd yet")


if __name__ == "__main__":
    main()