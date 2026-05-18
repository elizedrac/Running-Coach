# Running-Coach

## Steps to Run
1. python(3) -m venv venv
2. source venv/bin/activate
3. pip install -r requirements.txt
4. cli (local): python(3) cli.py (--debug to see llm + tool outputs)
5. ui (local): venv/bin/uvicorn main:app --reload --port 8000

## Tests
1. pytest tests/test_deterministic.py