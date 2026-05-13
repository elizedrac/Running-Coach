# Pydantic ToolPlan model for validating planner LLM JSON output (path, tools, args).
from pydantic import BaseModel
from typing import Literal

class ToolPlan(BaseModel):
    name: str
    args: dict = {}

class PlannerOutput(BaseModel):
    reasoning: str
    path: Literal["no_tools", "tools"]
    tools: list[ToolPlan] = []

# For SQL selector
class SQLPlan(BaseModel):
    queries: list[str]
    




