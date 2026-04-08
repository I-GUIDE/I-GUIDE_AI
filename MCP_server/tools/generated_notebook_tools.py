import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from server import mcp_tool


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from notebook_workflow_builder import list_manifest_paths


_REGISTERED_TOOL_NAMES: set[str] = set()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        try:
            return _json_safe(value.to_dict())
        except Exception:
            pass
    if hasattr(value, "tolist") and callable(value.tolist):
        try:
            return value.tolist()
        except Exception:
            pass
    return repr(value)


def _read_manifest(manifest_path: Path) -> Dict[str, Any]:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _load_namespace(source_path: Path) -> Dict[str, Any]:
    source = source_path.read_text(encoding="utf-8")
    namespace: Dict[str, Any] = {"__name__": f"generated_notebook_{source_path.stem}"}
    exec(compile(source, str(source_path), "exec"), namespace, namespace)
    return namespace


def _run_generated_manifest(manifest: Dict[str, Any], parameters_json: str = "{}") -> Dict[str, Any]:
    params = json.loads(parameters_json or "{}")
    if not isinstance(params, dict):
        raise ValueError("parameters_json must decode to a JSON object")

    source_path = Path(str(manifest["source_path"])).resolve()
    if not source_path.exists():
        raise ValueError(f"generated source file does not exist: {source_path}")

    mode = str(manifest.get("mode") or "script")
    if mode == "function":
        namespace = _load_namespace(source_path)
        entrypoint = str(manifest.get("entrypoint") or "").strip()
        target = namespace.get(entrypoint)
        if not callable(target):
            raise ValueError(f"entrypoint is not callable: {entrypoint}")
        result = target(**params)
        return {
            "workflow_id": manifest.get("workflow_id"),
            "tool_name": manifest.get("tool_name"),
            "mode": "function",
            "entrypoint": entrypoint,
            "parameters": params,
            "result": _json_safe(result),
        }

    namespace: Dict[str, Any] = {"__name__": f"generated_notebook_{source_path.stem}"}
    namespace.update(params)
    source = source_path.read_text(encoding="utf-8")
    exec(compile(source, str(source_path), "exec"), namespace, namespace)

    output_names = list(manifest.get("script_output_candidates") or [])
    outputs = {name: _json_safe(namespace[name]) for name in output_names if name in namespace}
    return {
        "workflow_id": manifest.get("workflow_id"),
        "tool_name": manifest.get("tool_name"),
        "mode": "script",
        "parameters": params,
        "outputs": outputs,
        "available_outputs": output_names,
    }


def register_generated_tool_from_manifest(manifest_path, *, register_with_server: bool = False) -> str:
    path = Path(manifest_path).resolve()
    manifest = _read_manifest(path)
    tool_name = str(manifest.get("tool_name") or "").strip()
    if not tool_name:
        raise ValueError(f"manifest is missing tool_name: {path}")

    if tool_name in _REGISTERED_TOOL_NAMES:
        return tool_name

    @mcp_tool(
        description=(
            f"Generated from uploaded notebook {Path(str(manifest.get('notebook_path') or '')).name}. "
            "Pass parameters_json as a JSON object string. "
            f"Execution mode: {manifest.get('mode')}."
        )
    )
    def _generated_tool(parameters_json: str = "{}") -> Dict[str, Any]:
        return _run_generated_manifest(manifest, parameters_json=parameters_json)

    _generated_tool.__name__ = tool_name
    globals()[tool_name] = _generated_tool
    _REGISTERED_TOOL_NAMES.add(tool_name)

    if register_with_server:
        try:
            from server import register_tool_with_mcp

            register_tool_with_mcp(_generated_tool)
        except Exception:
            pass

    return tool_name


def _load_all_generated_tools() -> List[str]:
    loaded: List[str] = []
    for manifest_path in list_manifest_paths():
        try:
            loaded.append(register_generated_tool_from_manifest(manifest_path))
        except Exception:
            continue
    return loaded


LOADED_GENERATED_TOOL_NAMES = _load_all_generated_tools()
