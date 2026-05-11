# Orchestrator. ask(question, user_id): single-shot planner + dispatch (no_tools / sql / tools).
from services.planner import planner
from services.final import final_output

TOOL_REGISTRY = {}

def call_tool(name: str, args: dict, user_id: str):
    fn = TOOL_REGISTRY.get(name)
    if not fn:
        return f"Tool '{name}' not yet implemented"
    return fn(user_id, **args)

def orchestrate(user_query, user_id):
    planner_response = planner(user_query)

    path = planner_response.path
    tool_results = {}

    if path == "tools": 
        for tool in planner_response.tools:
            name = tool.name.strip()
            result = call_tool(name, tool.args, user_id)
            tool_results[name] = result
    
    final_response = final_output(user_query, planner_response, tool_results)

    return final_response
        


