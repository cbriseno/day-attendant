"""Small, bounded stdio client for the installed Codex app-server.

The product owns all mutations. This process receives selected synthetic facts,
has no command/MCP/browser tools, and never receives arbitrary RPCs from the UI.
"""
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
import tomllib

from .errors import AssistantError
from .profiles import DEFAULT_EFFORT

from .instructions import PERSONAL_ASSISTANT as INSTRUCTIONS


def restricted_command(binary=None):
    binary = binary or shutil.which("codex")
    if not binary:
        raise AssistantError("agent_missing", "Codex is not installed.")
    disabled = ("shell_tool", "apps", "plugins", "hooks", "multi_agent", "multi_agent_v2",
                "memories", "browser_use", "browser_use_external", "computer_use",
                "image_generation", "view_image", "skill_search", "goals", "sleep_tool")
    values = {f"features.{name}": "false" for name in disabled}
    values.update({"web_search": '"disabled"', "memories.generate_memories": "false",
                   "memories.use_memories": "false", "project_doc_max_bytes": "0",
                   "features.skip_host_skill_discovery": "true",
                   "forced_login_method": '"chatgpt"', "model_provider": '"openai"',
                   "notify": "[]"})
    # Read names only; never copy credentials or modify the global configuration.
    config_path = Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))) / 'config.toml'
    if config_path.exists():
        config = tomllib.loads(config_path.read_text())
        for name in config.get("mcp_servers", {}):
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise AssistantError("agent_config_unsupported", "An MCP name requires a reviewed isolation adjustment.")
            prefix = "mcp_servers." + name
            values[prefix + ".enabled"] = "false"
            values[prefix + ".required"] = "false"
    command = [str(binary), "app-server", "--listen", "stdio://"]
    for key, value in values.items():
        command.extend(["-c", f"{key}={value}"])
    return command


class CodexBridge:
    provider="codex"
    def __init__(self, workspace, model=None, binary=None, effort=None):
        self.workspace = str(Path(workspace).resolve())
        self.command = restricted_command(binary)
        self.model = model
        self.sequence = 0
        self.pending = {}
        self.subscribers = []
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.closed = False
        self.events = []
        self.stderr_tail = ""
        self.process = subprocess.Popen(self.command, cwd=self.workspace, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                        bufsize=1, start_new_session=True)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()
        try:
            self.call("initialize", {"clientInfo": {"name": "day_attendant", "title": "Day Attendant", "version": "0.2.0"}}, 30)
            self.send({"method": "initialized", "params": {}})
            account = self.call("account/read", {"refreshToken": False})
            if (account.get("account") or {}).get("type") != "chatgpt":
                raise AssistantError("agent_auth_required", "Existing ChatGPT authentication is required; API fallback is disabled.")
            self.authentication = "chatgpt"
            catalog = self.call("model/list", {})["data"]
            if self.model and not any(self.model in (m.get('model'),m.get('id')) for m in catalog):
                raise AssistantError('agent_model_unavailable','The workspace model is unavailable; no replacement was selected.')
            if not self.model:
                selected = next((m for m in catalog if m.get("isDefault")), catalog[0])
                self.model = selected.get("model") or selected["id"]
            self.catalog = [{"model": m.get("model") or m["id"], "isDefault": bool(m.get("isDefault")),
                             "efforts": [o["reasoningEffort"] for o in m.get("supportedReasoningEfforts") or []],
                             "defaultEffort": m.get("defaultReasoningEffort")} for m in catalog]
            entry = next(e for m, e in zip(catalog, self.catalog) if self.model in (m.get("model"), m.get("id")))
            if effort and entry["efforts"] and effort not in entry["efforts"]:
                raise AssistantError("agent_effort_unavailable", "The selected model does not offer this reasoning level; no other level was selected.")
            # An unset level keeps the historical default when the model offers it.
            self.effort = effort or (DEFAULT_EFFORT if not entry["efforts"] or DEFAULT_EFFORT in entry["efforts"] else entry["defaultEffort"] or entry["efforts"][0])
            self.server_version = subprocess.check_output([self.command[0], "--version"], text=True, timeout=10).strip()
            self.assert_restricted(self.call('config/read',{'includeLayers':False})['config'])
        except BaseException:
            self.close()
            raise

    @staticmethod
    def assert_restricted(config):
        disabled=('shell_tool','apps','plugins','hooks','multi_agent','multi_agent_v2','memories','browser_use','browser_use_external','computer_use','image_generation','view_image','skill_search','goals','sleep_tool')
        features=config.get('features',{})
        valid=all(features.get(k) is False for k in disabled)
        valid=valid and features.get('skip_host_skill_discovery') is True
        valid=valid and config.get('web_search')=='disabled' and config.get('project_doc_max_bytes')==0 and config.get('notify',[])==[]
        valid=valid and all(s.get('enabled',True) is False for s in config.get('mcp_servers',{}).values())
        valid=valid and config.get('memories',{}).get('use_memories') is False and config.get('memories',{}).get('generate_memories') is False
        if not valid:raise AssistantError('agent_isolation_unverified','Effective Codex restrictions do not match this workspace contract. No conversation was started.')

    def _stderr(self):
        for line in self.process.stderr:
            self.stderr_tail = (self.stderr_tail + line)[-4000:]

    @property
    def available(self):
        return not self.closed and self.process.poll() is None

    def _read(self):
        try:
            for line in self.process.stdout:
                if len(line) > 4_000_000:
                    raise ValueError("Oversized app-server message")
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if "id" in message and "method" not in message:
                    with self.lock:
                        waiting = self.pending.get(message["id"])
                    if waiting:
                        waiting.put(message)
                elif "id" in message:
                    # No host tools, approvals or arbitrary RPC forwarding.
                    self.send({"id": message["id"], "error": {"code": -32601, "message": "This client exposes no host tools or approvals."}})
                    self._notify({"method": "bridge/deniedRequest", "params": {"method": message["method"]}})
                else:
                    self._notify(message)
        finally:
            with self.lock:
                waiting = list(self.pending.values())
            for target in waiting:
                target.put({"error": {"message": "The assistant process ended."}})
            self._notify({"method": "bridge/closed", "params": {}})

    def _notify(self, message):
        method = message.get("method", "")
        params = message.get("params", {})
        entry = {"method": method, "threadId": params.get("threadId"), "turnId": params.get("turnId")}
        if method == "item/completed":
            entry["itemType"] = params.get("item", {}).get("type")
        self.events.append(entry)
        self.events = self.events[-2000:]
        with self.lock:
            listeners = list(self.subscribers)
        for listener in listeners:
            listener.put(message)

    def send(self, message):
        with self.write_lock:
            try:
                self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError) as error:
                raise AssistantError("agent_disconnected", "The assistant disconnected.") from error

    def call(self, method, params, timeout=20):
        target = queue.Queue()
        with self.lock:
            self.sequence += 1
            identity = self.sequence
            self.pending[identity] = target
        try:
            self.send({"id": identity, "method": method, "params": params})
            try:
                response = target.get(timeout=timeout)
            except queue.Empty as error:
                raise AssistantError("agent_timeout", "The assistant did not respond in time.") from error
            if "error" in response:
                raise AssistantError("agent_error", str(response["error"].get("message", "Assistant error"))[:400])
            return response["result"]
        finally:
            with self.lock:
                self.pending.pop(identity, None)

    def thread(self, previous=None):
        # Every request starts an ephemeral native thread; the profile supplies the conversation.
        options = {"cwd": self.workspace, "sandbox": "read-only", "approvalPolicy": "never",
                   "model": self.model, "baseInstructions": INSTRUCTIONS, "ephemeral": True,
                   "developerInstructions": "Selected data only. No tools or external effects."}
        result = self.call("thread/start", options, 40)
        if str(Path(result["thread"]["cwd"]).resolve()) != self.workspace:
            raise AssistantError("agent_workspace_mismatch", "The agent did not use the selected synthetic workspace.")
        return result["thread"]["id"]

    def turn(self, thread_id, prompt, schema, timeout=120, canceled=None, started=None, images=None):
        target = queue.Queue()
        with self.lock:
            self.subscribers.append(target)
        turn_id = None
        begin = time.monotonic()
        try:
            result = self.call("turn/start", {"threadId": thread_id,
                "input": [{"type": "text", "text": prompt}]+[{"type":"localImage","path":str(Path(image["path"]).resolve())} for image in images or []], "outputSchema": schema,
                "effort": self.effort}, 40)
            turn_id = result["turn"]["id"]
            if started:
                started(turn_id)
            final_text = None
            while time.monotonic() - begin < timeout:
                if canceled and canceled.is_set():
                    self.interrupt(thread_id, turn_id)
                    raise AssistantError("agent_interrupted", "The evaluation was canceled.")
                try:
                    event = target.get(timeout=0.2)
                except queue.Empty:
                    continue
                method, params = event.get("method"), event.get("params", {})
                if method in ("bridge/closed", "bridge/deniedRequest"):
                    raise AssistantError("agent_disconnected", "The assistant ended or attempted an unavailable operation.")
                if params.get("threadId") != thread_id:
                    continue
                if params.get("turnId") not in (None, turn_id):
                    continue
                if method == "item/completed":
                    item = params["item"]
                    if item.get("type") == "agentMessage":
                        final_text = item.get("text")
                    elif item.get("type") not in ("userMessage", "reasoning", "plan"):
                        self.interrupt(thread_id, turn_id)
                        raise AssistantError("agent_tool_attempt", "This workflow does not allow assistant tools.")
                if method == "turn/completed" and params.get("turn", {}).get("id") == turn_id:
                    if params["turn"]["status"] != "completed":
                        raise AssistantError("agent_incomplete", "The assistant turn did not finish successfully.",
                            {"thread_id":thread_id,"turn_id":turn_id,"status":params['turn']['status'],"provider_error":params['turn'].get('error')})
                    try:
                        output = json.loads(final_text)
                    except (ValueError, TypeError) as error:
                        raise AssistantError("agent_invalid_output", "The assistant returned an invalid response.") from error
                    return {"output": output, "thread_id": thread_id, "turn_id": turn_id,
                            "model": self.model, "seconds": round(time.monotonic()-begin, 3), "status": "completed","provider":self.provider}
            self.interrupt(thread_id, turn_id)
            raise AssistantError("agent_timeout", "The contextual review timed out.")
        finally:
            with self.lock:
                self.subscribers.remove(target)

    def interrupt(self, thread_id, turn_id):
        try:
            self.call("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, 10)
        except AssistantError:
            pass  # The domain invalidates the generation before this best effort.

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except OSError:
                pass
