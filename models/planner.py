# Pydantic ToolPlan model for validating planner LLM JSON output (path, tools, args).
from pydantic import BaseModel
from typing import Literal
from dataclasses import dataclass, field

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

class CourseDetailsPlan(BaseModel):
    location: str
    race: str
    query: str
    details: str
    
@dataclass    
class History:
    summary: str = ""
    recent: list[dict] = field(default_factory=list)
    turn_count: int = 0




