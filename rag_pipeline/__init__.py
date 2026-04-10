from dotenv import load_dotenv
load_dotenv(dotenv_path=".env.local", override=True)

from importlib import import_module

__all__ = [
    "run_pipeline",
]

_EXPORT_MAP = {
    "run_pipeline": (".pipeline", "run_pipeline"),
}


def __getattr__(name: str):
    target = _EXPORT_MAP.get(name)
    if target is None:
        raise AttributeError(f"module 'rag_pipeline' has no attribute '{name}'")
    module_name, attr_name = target
    module = import_module(module_name, package=__name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
