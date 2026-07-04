from .types import CommandResult, EntryInfo, Mount, S3Config, SandboxInfo

__all__ = [
    # current API
    "Sandbox",
    "Shell",
    "Shells",
    "CommandHandle",
    # data types
    "EntryInfo",
    "CommandResult",
    "SandboxInfo",
    "Mount",
    "S3Config",
    "PortForwarding",
]

# Heavy modules are lazy-loaded so lightweight entry points (the yr-sandbox CLI)
# don't pay for the httpx/websockets import up front.
_lazy_imports = {
    "Sandbox": ".sandbox_api",
    "Shell": ".shell",
    "Shells": ".shell",
    "CommandHandle": ".commands",
    "PortForwarding": ".types",
}


def __getattr__(name):
    module_path = _lazy_imports.get(name)
    if module_path is not None:
        import importlib

        module = importlib.import_module(module_path, __package__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
