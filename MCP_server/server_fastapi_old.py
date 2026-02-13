from fastapi import FastAPI, Request
from pydantic import BaseModel
import os
import importlib.util
from dotenv import load_dotenv

# Load .env from root folder
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

# --- MCP tool decorator (simulated) ---
def mcp_tool(
    _func=None,
    *,
    summary: str | None = None,
    description: str | None = None,
    tool_description: str | None = None,
    mcp_description: str | None = None,
):
    def decorator(func):
        func._is_mcp_tool = True
        if summary:
            func._mcp_summary = summary
            func._tool_summary = summary
        if description:
            func._tool_description = description
            func._mcp_description = description
        if tool_description:
            func._tool_description = tool_description
        if mcp_description:
            func._mcp_description = mcp_description
        return func

    if callable(_func):
        return decorator(_func)
    return decorator

# FastAPI app
app = FastAPI(title="I-GUIDE MCP API")

# --- Helper to register tools ---
async def _parse_request_inputs(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        inputs: dict = {}
        for key, value in form.multi_items():
            if key in inputs:
                existing = inputs[key]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    inputs[key] = [existing, value]
            else:
                inputs[key] = value
        return inputs
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def register_tool(func):
    tool_name = func.__name__
    raw_doc = (func.__doc__ or "").strip()
    default_summary = raw_doc.splitlines()[0].strip() if raw_doc else tool_name
    summary = getattr(func, "_tool_summary", None) or default_summary
    description = getattr(func, "_tool_description", None) or (raw_doc if raw_doc else None)
    mcp_summary = getattr(func, "_mcp_summary", None) or default_summary
    mcp_description = getattr(func, "_mcp_description", None) or (raw_doc if raw_doc else None)

    @app.post(f"/tool/{tool_name}", summary=summary, description=description)
    async def tool_api(request: Request):
        inputs = await _parse_request_inputs(request)
        return func(**inputs)

    @app.post(
        f"/mcp/{tool_name}",
        summary=mcp_summary,
        description=(
            f"{mcp_description}\n\nReturns an MCP envelope with tool, inputs, outputs, and status."
            if mcp_description
            else "Returns an MCP envelope with tool, inputs, outputs, and status."
        ),
    )
    async def tool_mcp(request: Request):
        inputs = await _parse_request_inputs(request)
        return {
            "tool": tool_name,
            "inputs": inputs,
            "outputs": func(**inputs),
            "status": "success"
        }

# --- Auto-scan tools folder ---
tools_dir = os.path.join(os.path.dirname(__file__), "tools")
for filename in os.listdir(tools_dir):
    if filename.endswith(".py") and not filename.startswith("__"):
        module_path = os.path.join(tools_dir, filename)
        spec = importlib.util.spec_from_file_location(filename[:-3], module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Register functions decorated as MCP tools
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if callable(attr) and getattr(attr, "_is_mcp_tool", False):
                register_tool(attr)

print(f"Discovered MCP tools from {tools_dir}")
