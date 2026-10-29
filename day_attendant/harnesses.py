"""Small provider boundary. Domain code never selects or falls back a provider."""
from typing import Protocol
from .errors import AssistantError


class Harness(Protocol):
    provider: str
    model: str | None
    available: bool
    effort: str
    def thread(self): ...
    def turn(self, thread_id, prompt, schema, timeout=None, canceled=None, images=None): ...
    def close(self): ...


def context_usage(used, window):
    """Context occupied by a turn, exactly as its harness reported it; None when unreported."""
    count=lambda value:value if isinstance(value,int) and not isinstance(value,bool) and value>=0 else None
    used,window=count(used),count(window)
    if used is None:return None
    # A zero or missing window is unknown, never an empty window.
    return {'contextTokens':used,'contextWindow':window or None}


def discover_catalog(provider, workspace):
    """Model choices for a harness. Codex lists them through model/list; no turn is started."""
    if provider=='claude':
        from .claudebridge import catalog
        return catalog()
    bridge=create_harness(provider,workspace)
    try:return bridge.catalog
    finally:bridge.close()


def create_harness(provider, workspace, model=None, effort=None):
    if provider=='codex':
        from .codexbridge import CodexBridge
        return CodexBridge(workspace,model=model,effort=effort)
    if provider=='claude':
        from .claudebridge import ClaudeBridge
        return ClaudeBridge(workspace,model=model,effort=effort)
    raise AssistantError('invalid_harness','Choose codex or claude explicitly.')
