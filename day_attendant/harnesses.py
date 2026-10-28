"""Small provider boundary. Domain code never selects or falls back a provider."""
from typing import Protocol
from .errors import AssistantError


class Harness(Protocol):
    provider: str
    model: str | None
    available: bool
    effort: str
    def thread(self, previous=None): ...
    def turn(self, thread_id, prompt, schema, timeout=120, canceled=None, images=None): ...
    def close(self): ...


def create_harness(provider, workspace, model=None, effort=None):
    if provider=='codex':
        from .codexbridge import CodexBridge
        return CodexBridge(workspace,model=model,effort=effort)
    if provider=='claude':
        from .claudebridge import ClaudeBridge
        return ClaudeBridge(workspace,model=model,effort=effort)
    raise AssistantError('invalid_harness','Choose codex or claude explicitly.')
