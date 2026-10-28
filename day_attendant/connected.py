"""Persistent proposals for an explicitly initialized synthetic workspace.

Taskwarrior remains the task authority. A proposal cannot be recovered as an
operation until admission and its authorization are committed together.
"""
from datetime import timedelta
import json
import math
import threading
import uuid

from .errors import AssistantError
from .service import Assistant, encoded, fingerprint
from .taskwarrior import canonical
from .timeutil import instant, parse, stamp, wire

REVIEW_SCHEMA = {"type": "object", "properties": {
    "verdict": {"type": "string", "enum": ["allow", "review", "insufficient"]},
    "message": {"type": "string"},
    "concerns": {"type": "array", "items": {"type": "object", "properties": {
        "code": {"type": "string", "enum": ["capacity", "after_due", "contextual", "rest_goal"]},
        "message": {"type": "string"}, "refs": {"type": "array", "items": {"type": "string"}}},
        "required": ["code", "message", "refs"], "additionalProperties": False}},
    "uncertainties": {"type": "array", "items": {"type": "string"}},
    "alternatives": {"type": "array", "items": {"type": "string"}}},
    "required": ["verdict", "message", "concerns", "uncertainties", "alternatives"], "additionalProperties": False}

ALLOWED = {
    "task_add": {"title", "priority", "due", "source", "zone", "person", "reference", "remainingMinutes", "awaiting", "context", "next"},
    "task_update": {"uuid", "title", "priority", "due", "zone", "progress"},
    "front_activate": {"uuid"}, "front_todo": {"uuid"}, "front_retire": {"uuid"},
    "task_snooze": {"uuid", "until", "zone", "fold"}, "task_done": {"uuid"},
    "context_update": {"availableMinutes", "availableUntil", "rest"},
    "continuity_update": {"uuid", "remainingMinutes", "awaiting", "context", "next"},
}
PENDING = ("evaluating", "review", "unavailable", "stale", "invalid")


class Connected:
    def __init__(self, app, bridge=None):
        self.app = app
        self.store = app.store
        self.bridge = bridge
        self.inflight = {}
        self.inflight_lock = threading.Lock()

    def initialize(self):
        """Opt-in only, never auto-migrate personal task data."""
        with self.store.lock():
            if self.store.state_value("connected", False):
                with self.store.connect() as con:
                    if 'generation' not in {r[1] for r in con.execute('PRAGMA table_info(messages)')}:
                        con.execute('ALTER TABLE messages ADD COLUMN generation INTEGER NOT NULL DEFAULT 0')
                return
            if self.store.unresolved():
                raise AssistantError("migration_recovery_required", "Recover operations with the previous version before connecting this workspace.")
            if self.app.tasks.rows() and not self.store.state_value("synthetic_bootstrap", False):
                raise AssistantError("synthetic_state_required", "This release requires a new synthetic workspace separate from personal data.")
            with self.store.connect() as con:
                con.executescript('''
                    CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL,
                      digest TEXT NOT NULL, intent TEXT NOT NULL, entity TEXT NOT NULL, origin TEXT NOT NULL,
                      request_text TEXT NOT NULL, phase TEXT NOT NULL, generation INTEGER NOT NULL,
                      fingerprint TEXT, facts TEXT NOT NULL, review TEXT, error TEXT,
                      operation_key TEXT UNIQUE, result TEXT, created_at TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS decisions(operation_key TEXT PRIMARY KEY,
                      proposal_id TEXT NOT NULL, generation INTEGER NOT NULL, intent_digest TEXT NOT NULL,
                      context_digest TEXT NOT NULL, acknowledged TEXT NOT NULL, review_complete INTEGER NOT NULL, at TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS evaluations(id INTEGER PRIMARY KEY AUTOINCREMENT,
                      proposal_id TEXT NOT NULL, generation INTEGER NOT NULL, status TEXT NOT NULL,
                      input TEXT NOT NULL, result TEXT, at TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS continuity(uuid TEXT PRIMARY KEY, data TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS acknowledgments(signature TEXT PRIMARY KEY, code TEXT NOT NULL, at TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS facts(id INTEGER PRIMARY KEY AUTOINCREMENT, operation_key TEXT UNIQUE NOT NULL,
                      proposal_id TEXT NOT NULL, action TEXT NOT NULL, plan TEXT NOT NULL, at TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,
                      key TEXT UNIQUE NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL,
                      data TEXT, at TEXT NOT NULL);
                ''')
                con.execute("INSERT OR IGNORE INTO state VALUES('synthetic_bootstrap','true')")
                con.execute("INSERT OR IGNORE INTO state VALUES('bootstrap_clock',?)", (encoded(self.app.now()),))
                if 'generation' not in {r[1] for r in con.execute('PRAGMA table_info(messages)')}:
                    con.execute('ALTER TABLE messages ADD COLUMN generation INTEGER NOT NULL DEFAULT 0')
        # Existing journal/idempotency handles a crash during the fictional seed.
        start = parse(self.store.state_value("bootstrap_clock"))
        seeds = [
            ("[SYNTHETIC] Review the deployment proposal", "high", 8, 40, False, "You stopped at the rollback risks.", "Review that section."),
            ("[SYNTHETIC] Reply to Ana about the review", "high", 8, 40, False, "You already have a draft.", "Review and send the draft."),
            ("[SYNTHETIC] Prepare two points for the meeting", "medium", 24, 10, False, "The meeting is tomorrow.", "Choose two points."),
            ("[SYNTHETIC] Investigate the cache alternative", "low", 48, 25, True, "You are waiting for a reply before continuing.", "Review the reply when it arrives."),
        ]
        for index, (title, priority, hours, remaining, awaiting, context, next_step) in enumerate(seeds):
            uid = self.app.mutate("task_add", {"title": title, "priority": priority,
                "due": stamp(start+timedelta(hours=hours)), "source": "general"}, f"bootstrap-task-{index}")["uuid"]
            self.app.mutate("continuity_update", {"uuid": uid, "remainingMinutes": remaining,
                "awaiting": awaiting, "context": context, "next": next_step}, f"bootstrap-continuity-{index}")
            if index == 0:
                self.app.mutate("front_activate", {"uuid": uid}, "bootstrap-front-0")
        with self.store.lock(), self.store.connect() as con:
            con.execute("INSERT OR REPLACE INTO state VALUES('connected','true')")
            con.execute("PRAGMA user_version=3")

    def startup(self):
        with self.store.lock():
            unresolved = self.app._recover()
            if unresolved:
                raise AssistantError("recovery_required", "An admitted operation requires reconciliation.", {"operations": unresolved})
            with self.store.connect() as con:
                con.execute("UPDATE proposals SET phase='unavailable',generation=generation+1,error=? WHERE phase='evaluating'",
                            ("The review was interrupted by a restart. Retry it or make an explicit decision.",))
                con.execute("UPDATE evaluations SET status='interrupted' WHERE status='running'")
                pending_messages=[dict(r) for r in con.execute("SELECT * FROM messages WHERE role='user' AND status='pending'")]
                for message in pending_messages:
                    con.execute("INSERT OR IGNORE INTO messages(key,role,text,status,data,at) VALUES(?,'assistant',?,'incomplete',?,?)",
                        (message['key']+':assistant','The response was interrupted by a restart. Retry it explicitly; any existing action retains its own state.',encoded({'code':'restart_interrupted'}),self.app.now()))
                con.execute("UPDATE messages SET status='incomplete',generation=generation+1 WHERE status IN ('pending','evaluating')")

    @staticmethod
    def unpack(row):
        if row is None:
            raise AssistantError("proposal_not_found", "The proposal does not exist.")
        result = dict(row)
        for key in ("intent", "facts", "review", "result"):
            result[key] = json.loads(result[key]) if result.get(key) is not None else None
        return result

    def proposal(self, identity):
        with self.store.connect() as con:
            return self.unpack(con.execute("SELECT * FROM proposals WHERE id=?", (identity,)).fetchone())

    def _snapshot(self):
        self.app.clean_read()
        rows = self.app._view()
        with self.store.connect() as con:
            continuation = {r["uuid"]: json.loads(r["data"]) for r in con.execute("SELECT * FROM continuity")}
            proposals = [self.unpack(r) for r in con.execute("SELECT * FROM proposals ORDER BY rowid")]
            facts = [dict(r) for r in con.execute("SELECT * FROM facts ORDER BY id")]
            messages = [dict(r) for r in con.execute("SELECT * FROM messages ORDER BY id DESC LIMIT 100")][::-1]
        tasks = []
        for row in rows:
            extra = continuation.get(row["uuid"], {})
            state = "done" if row["status"] == "completed" else "retired" if row["status"] == "deleted" else "snoozed" if row["status"] == "waiting" else "active" if row["focused"] else "todo"
            tasks.append({"id": row["uuid"], "title": row["title"], "state": state,
                "priority": row["priority"], "due": row["due_at"], "until": row["snoozed_until"],
                "blocked": row["status"] == "blocked", "context": extra.get("context", ""), "next": extra.get("next", ""),
                "remainingMinutes": extra.get("remainingMinutes"), "awaiting": extra.get("awaiting", False),
                "progressRecordedAt": row["metadata"].get("human_progress_at")})
        return {"tasks": tasks, "context": self.store.state_value("day_context", {"availableMinutes": None, "availableUntil": None, "rest": False}),
                "timezone": self.store.state_value('timezone','America/Edmonton'),
                "proposals": proposals, "events": facts, "messages": messages,
                "revision": self.store.revision(), "now": self.app.now(), "synthetic": True,
                "agent": {"connected": self.bridge is not None and getattr(self.bridge,'available',True),
                          "model": self.bridge.model if self.bridge else self.store.state_value('agent_model'),
                          "error":self.store.state_value('agent_startup_error')}}

    def snapshot(self):
        with self.store.lock():
            return self._snapshot()

    def _plan(self, intent, key):
        if intent["action"] not in ALLOWED and intent["action"] != "task_restore":
            raise AssistantError("unsupported_action", "This action is not supported in this release.")
        return self.app._plan(intent["action"], intent["payload"], key)

    def _capacity(self, tasks, context):
        minutes, until = context.get("availableMinutes"), context.get("availableUntil")
        if minutes is None or not until or parse(until) <= self.app.clock():
            return None
        active = [t for t in tasks if t["state"] == "active" and not t["awaiting"] and t["due"] and t["due"] <= until and t["remainingMinutes"] is not None]
        for deadline in sorted({t["due"] for t in active}):
            participants = sorted([t for t in active if t["due"] <= deadline], key=lambda t: t["id"])
            required = sum(t["remainingMinutes"] for t in participants)
            anchor = context.get("declaredAt") or self.store.state_value("bootstrap_clock")
            possible = max(0, min(minutes, (parse(deadline)-self.app.clock()).total_seconds()/60))
            if required > possible:
                basis = {"participants": [[t["id"],t["due"],t["remainingMinutes"]] for t in participants],
                         "availableMinutes": minutes, "availableUntil": until, "declaredAt": anchor, "deadline": deadline,
                         "remainingWindowBand": math.ceil(possible/5)}
                return {"signature": fingerprint(["capacity", basis]), "required": required, "possible": round(possible,2), "participants": participants}
        return None

    @staticmethod
    def consequence(item, inputs):
        value = dict(item)
        refs = set(item['refs'])
        if item['code']=='capacity' and refs and refs <= {'capacity.requiredMinutes','capacity.availableMinutes'} and inputs.get('capacity_signature'):
            signature = inputs['capacity_signature']
        else:
            signature = fingerprint([item['code'],[(ref,inputs['evidence'][ref]) for ref in sorted(refs)]])
        value['signature'] = signature
        return value

    def _review_expired(self, inputs):
        return (self.app.clock()-parse(inputs['observed_at'])).total_seconds() >= 120

    def _input(self, proposal, snapshot=None):
        snapshot = snapshot or self._snapshot()
        intent = proposal["intent"]
        plan = self._plan(intent, proposal["key"])
        target_id = intent["payload"].get("uuid")
        current = next((t for t in snapshot["tasks"] if t["id"] == target_id), None)
        proposed = [dict(t) for t in snapshot["tasks"]]
        target = next((t for t in proposed if t["id"] == target_id), None)
        action = intent["action"]
        if target:
            if action == "front_activate": target.update(state="active", until=None)
            if action == "front_todo": target.update(state="todo", until=None)
            if action == "task_snooze": target.update(state="snoozed", until=instant(intent["payload"]["until"], intent["payload"].get("zone"), intent["payload"].get("fold")))
            if action == "task_done": target.update(state="done", until=None)
            if action == "front_retire": target.update(state="retired", until=None)
            if action in ("task_update", "task_restore"):
                after = plan["steps"][0]["after"] if plan["steps"] else None
                if after:
                    target.update(title=after["description"], due=stamp(parse(after["due"])) if after.get("due") else None,
                        until=stamp(parse(after["wait"])) if after.get("wait") else None,
                        state="done" if after["status"]=="completed" else "retired" if after["status"]=="deleted" else "snoozed" if after.get("wait") and parse(after["wait"])>self.app.clock() else "active" if after.get("start") else "todo")
        evidence = {"context.rest": snapshot["context"].get("rest", False)}
        for k in ("availableMinutes", "availableUntil"):
            if snapshot["context"].get(k) is not None: evidence["context."+k] = snapshot["context"][k]
        for t in snapshot["tasks"]:
            if t["id"] == target_id or t["state"] == "active":
                for k in ("title", "state", "due", "until", "remainingMinutes", "awaiting", "context", "next", "progressRecordedAt"):
                    if t.get(k) is not None: evidence["tasks."+t["id"]+"."+k] = t[k]
        concerns = []
        if target and target["until"] and target["due"] and target["until"] > target["due"]:
            evidence["proposal.until"] = target["until"]
            concerns.append({"code":"after_due", "message":"You would resume after the recorded deadline. Snoozing preserves that commitment.",
                "message_key":"risk_after_due","message_params":{},
                "refs":["proposal.until", "tasks."+target_id+".due"], "signature":fingerprint(["after_due",target_id,target["until"],target["due"]])})
        if target and target['until']:evidence['proposal.until']=target['until']
        capacity = self._capacity(proposed, snapshot["context"])
        existing = self._capacity(snapshot["tasks"], snapshot["context"])
        with self.store.connect() as con:
            acknowledged = {r[0] for r in con.execute("SELECT signature FROM acknowledgments")}
        already = bool(capacity and existing and capacity["signature"] == existing["signature"] and capacity["signature"] in acknowledged)
        if capacity:
            evidence.update({"capacity.requiredMinutes":capacity["required"],"capacity.availableMinutes":capacity["possible"]})
            if not already:
                concerns.append({"code":"capacity", "message":f"The fronts in this window require {capacity['required']:g} estimated minutes, compared with at most {capacity['possible']:g} available minutes. Some progress may be unrecorded.",
                    "message_key":"risk_capacity","message_params":{"required":capacity['required'],"possible":capacity['possible']},
                    "refs":["capacity.requiredMinutes","capacity.availableMinutes"],"signature":capacity["signature"]})
        history = [{"action": f["action"], "at": f["at"]} for f in snapshot["events"]
                   if json.loads(f["plan"]).get("target_uuid") == target_id][-8:]
        evidence["history.confirmed"] = history
        # A different unrelated todo/completed item doesn't invalidate this review.
        relevant = {"target": current, "intent": intent, "context": snapshot["context"],
                    "capacity": capacity and capacity["signature"], "history": history,
                    "availability_current": bool(snapshot['context'].get('availableUntil') and parse(snapshot['context']['availableUntil'])>self.app.clock()),
                    "temporal": [[t['id'], parse(t['due'])>self.app.clock(),
                        (parse(t['due'])-max(self.app.clock(),parse(t['until']) if t.get('until') else self.app.clock())).total_seconds()>=60*t['remainingMinutes']]
                        for t in proposed if t['due'] and t['remainingMinutes'] is not None and (t['id']==target_id or t['state']=='active' and not t['awaiting'])],
                    "active_work": [t for t in snapshot['tasks'] if t['state']=='active' and not t['awaiting']]}
        return {"proposal_id":proposal["id"], "generation":proposal["generation"], "intent":intent,"origin":proposal.get('origin','ui'),
            "authorization_source":"authenticated_typed_request" if proposal.get('origin','ui') in ('ui','cli') else "interpreted_human_message",
            "service_metadata":{'zone':intent['payload']['zone']} if intent['payload'].get('zone') else {},
            "user_request":proposal["request_text"], "observed_at":self.app.now(), "timezone":snapshot.get("timezone","America/Edmonton"),
            "tasks": [t for t in snapshot["tasks"] if t["id"]==target_id or t["state"]=="active"],
            "proposed_task":target, "context":snapshot["context"], "evidence":evidence,
            "known_concerns":[c for c in concerns if c['signature'] not in acknowledged], "capacityAlreadyReviewed":already,
            "capacity_signature":capacity and capacity['signature'],
            "acknowledged_consequences":self._acknowledged_context(evidence,acknowledged),
            "fingerprint":fingerprint(relevant), "plan":plan}

    def _acknowledged_context(self, evidence, signatures):
        with self.store.connect() as con:
            items=[item for row in con.execute('SELECT acknowledged FROM decisions') for item in json.loads(row[0])]
        result={}
        for item in items:
            refs=item.get('refs',[])
            if item['signature'] in signatures and item.get('basis')=={r:evidence.get(r) for r in refs}:
                result[item['signature']]=item
        return list(result.values())

    def request(self, action, payload, key, origin="ui", request_text="", expected_revision=None, inverse=False, message_guard=None):
        if not isinstance(key, str) or not 1 <= len(key) <= 200 or not isinstance(payload, dict):
            raise AssistantError("invalid_request", "The request requires content and a stable key.")
        if not isinstance(action,str) or action != "task_restore" and (action not in ALLOWED or set(payload)-ALLOWED[action]):
            raise AssistantError("unsupported_action", "Unsupported action or fields.")
        for name in ('uuid','title','priority','due','until','zone','source','person','reference','context','next'):
            if name in payload and payload[name] is not None and not isinstance(payload[name],str):
                raise AssistantError('invalid_request','Text fields must contain strings.')
        if payload.get('priority') is not None and payload['priority'] not in ('high','medium','low'):
            raise AssistantError('invalid_request','Invalid priority.')
        if action=='task_add' and (not isinstance(payload.get('title'),str) or not payload['title'].strip()):
            raise AssistantError('invalid_request','A capture requires an explicit title.')
        if 'progress' in payload and not isinstance(payload['progress'],bool):
            raise AssistantError('invalid_request','Progress requires an explicit declaration.')
        if action == "task_restore" and not inverse:
            raise AssistantError("invalid_restore", "Use Undo on a confirmed fact.")
        intent = {"action":action,"payload":payload}
        digest = fingerprint(intent)
        with self.store.lock():
            if message_guard:
                with self.store.connect() as con:
                    message=con.execute('SELECT status,generation FROM messages WHERE key=?',(message_guard[0],)).fetchone()
                if not message or message['status']!='pending' or message['generation']!=message_guard[1]:
                    raise AssistantError('stale_message','This conversation attempt is no longer current.')
            with self.store.connect() as con:
                old = con.execute("SELECT * FROM proposals WHERE key=?", (key,)).fetchone()
            if old:
                if old["digest"] != digest: raise AssistantError("idempotency_conflict", "This key identifies a different intent.")
                return self.unpack(old)
            self.app.clean_read()
            if expected_revision is not None and expected_revision != self.store.revision():
                raise AssistantError("stale_revision", "Read the current state before changing it.")
            plan = self._plan(intent, key)  # Validate before superseding any prior intent.
            identity = str(uuid.uuid4())
            entity = ("metadata:" if action=="continuity_update" else "task:")+payload["uuid"] if payload.get("uuid") else "context" if action=="context_update" else "new:"+key
            proposal = {"id":identity,"key":key,"intent":intent,"generation":0,"request_text":request_text,"origin":origin}
            inputs = self._input(proposal)
            phase = "evaluating" if plan["steps"] or plan["effect"] else "unchanged"
            with self.store.connect() as con:
                superseded = [r[0] for r in con.execute("SELECT id FROM proposals WHERE entity=? AND phase IN ('evaluating','review','unavailable','stale','invalid')",(entity,))]
                con.execute("UPDATE proposals SET phase='superseded',generation=generation+1 WHERE entity=? AND phase IN ('evaluating','review','unavailable','stale','invalid')",(entity,))
                con.execute("INSERT INTO proposals(id,key,digest,intent,entity,origin,request_text,phase,generation,fingerprint,facts,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identity,key,digest,encoded(intent),entity,origin,request_text,phase,0,inputs["fingerprint"],encoded(inputs["known_concerns"]),self.app.now()))
                if phase=='unchanged' and superseded:con.execute("UPDATE proposals SET result=? WHERE id=?",(encoded({'superseded':superseded}),identity))
            for old_id in superseded: self._cancel_inflight(old_id)
            if phase=="evaluating" and action in ("context_update","continuity_update") and origin in ('ui','cli'):
                self._admit_locked(identity, acknowledged=False, declaration=True)
            return self.proposal(identity)

    def _cancel_inflight(self, identity):
        with self.inflight_lock:
            active = [event for (proposal_id, _), event in self.inflight.items() if proposal_id==identity]
        for event in active:event.set()

    def cancel(self, identity):
        with self.store.lock(), self.store.connect() as con:
            proposal = self.proposal(identity)
            if proposal["phase"] in ("admitted","applied"):
                raise AssistantError("already_admitted", "Execution was already admitted; inspect the result and use Undo.")
            con.execute("UPDATE proposals SET phase='canceled',generation=generation+1 WHERE id=?",(identity,))
            self._cancel_inflight(identity)
        return self.proposal(identity)

    def retry(self, identity):
        with self.store.lock():
            proposal = self.proposal(identity)
            if proposal["phase"] not in ("unavailable","stale","review","invalid"):
                return proposal
            inputs = self._input(proposal)
            with self.store.connect() as con:
                con.execute("UPDATE proposals SET phase='evaluating',generation=generation+1,fingerprint=?,facts=?,review=NULL,error=NULL WHERE id=?",
                            (inputs["fingerprint"],encoded(inputs["known_concerns"]),identity))
            self._cancel_inflight(identity)
            return self.proposal(identity)

    def evaluate(self, identity):
        canceled = threading.Event()
        generation = None
        with self.store.lock():
            initial = self.proposal(identity)
            if initial["phase"] != "evaluating":return
            generation = initial["generation"]
        token = (identity,generation)
        with self.inflight_lock:
            if token in self.inflight: return
            self.inflight[token] = canceled
        attempt = None
        try:
            with self.store.lock():
                proposal = self.proposal(identity)
                if proposal["phase"] != "evaluating" or proposal["generation"]!=generation: return
                inputs = self._input(proposal)
                with self.store.connect() as con:
                    con.execute("UPDATE proposals SET fingerprint=?,facts=? WHERE id=?",(inputs["fingerprint"],encoded(inputs["known_concerns"]),identity))
                    attempt = con.execute("INSERT INTO evaluations(proposal_id,generation,status,input,at) VALUES(?,?,'running',?,?)",
                        (identity,generation,encoded({k:v for k,v in inputs.items() if k!='plan'}),self.app.now())).lastrowid
            if self.bridge is None:
                raise AssistantError("agent_unavailable", "The assistant is not connected.")
            thread = self.bridge.thread()
            prompt = """Review this pending intent before application; execute nothing. Ground consequences in concrete evidence, never in a universal active-front count. front_activate marks a responsibility as an active front for tracking and later alternation; it does NOT assert that its next step is executable now or that a reply arrived. awaiting=true alone is a legitimate active front and does not justify a barrier. Native blocking dependencies are checked by the service separately.
For authorization_source=authenticated_typed_request, the exact typed UI/CLI action is already the authenticated operator's explicit instruction. No second prose instruction is required. For interpreted_human_message, verify EVERY proposed field against the exact current human message. A quoted substring and userAuthorized are not sufficient proof of scope; extra fields, negated changes or ambiguity require insufficient and a concrete clarification. This also applies to context/continuity declarations. service_metadata.zone is the existing workspace timezone used to interpret an explicitly requested date, not a separate task-field edit requiring a second instruction.
Authorizing an intent never acknowledges its consequences. known_concerns are detected, UNACCEPTED conflicts. Address every known code and its exact evidence refs with verdict=review, never allow. acknowledged_consequences are specific accepted bases, not category-wide permission. capacityAlreadyReviewed refers ONLY to the same aggregate capacity_signature. Never suppress a distinct task or completion-window concern because it shares a category.
Respect legitimate rest, unrecorded progress, uncertainty and autonomy. Allow unambiguous captures and requests without a supported consequence, without routine confirmation. Use exact refs from evidence. Do not invent facts. Respond in the current human message's language; default to English for typed actions with no prose request.
"""+encoded({k:v for k,v in inputs.items() if k!='plan'})
            result = self.bridge.turn(thread,prompt,REVIEW_SCHEMA,canceled=canceled)
            review = self.validate_review(result["output"], inputs)
            with self.store.lock():
                current = self.proposal(identity)
                with self.store.connect() as con:
                    con.execute("UPDATE evaluations SET status='completed',result=? WHERE id=?",(encoded(result),attempt))
                if current["generation"] != generation or current["phase"] != "evaluating": return
                latest = self._input(current)
                if latest["fingerprint"] != inputs["fingerprint"] or self._review_expired(inputs):
                    with self.store.connect() as con:con.execute("UPDATE proposals SET phase='stale',generation=generation+1,error=? WHERE id=?",("Relevant context changed. Review the decision again.",identity))
                    return
                concerns = inputs["known_concerns"][:]
                for raw_item in review["concerns"]:
                    item=self.consequence(raw_item,inputs)
                    if any(c['signature']==item['signature'] for c in concerns):continue
                    with self.store.connect() as con:
                        if con.execute('SELECT 1 FROM acknowledgments WHERE signature=?',(item['signature'],)).fetchone():continue
                    concerns.append(item)
                phase = "unavailable" if review["verdict"]=="insufficient" else "review" if concerns else "ready"
                with self.store.connect() as con:
                    con.execute("UPDATE proposals SET phase=?,facts=?,review=?,error=NULL WHERE id=?",(phase,encoded(concerns),encoded(result),identity))
                if phase=="ready":self._admit_locked(identity)
        except Exception as error:
            with self.store.lock(), self.store.connect() as con:
                if attempt:con.execute("UPDATE evaluations SET status='failed',result=? WHERE id=? AND status='running'",(encoded({"code":getattr(error,'code','review_failed'),"message":str(error)[:400],"details":getattr(error,'details',None)}),attempt))
                proposal = self.proposal(identity)
                if proposal["phase"]=="evaluating" and proposal["generation"]==generation:con.execute("UPDATE proposals SET phase='unavailable',error=? WHERE id=?",(str(error)[:400],identity))
        finally:
            with self.inflight_lock:self.inflight.pop(token,None)

    @staticmethod
    def validate_review(review, inputs):
        if not isinstance(review,dict) or set(review)!=set(REVIEW_SCHEMA["required"]) or review["verdict"] not in ("allow","review","insufficient"):
            raise AssistantError("agent_invalid_output", "The review does not match the contract.")
        if not isinstance(review["message"],str) or not 1<=len(review["message"])<=3000:
            raise AssistantError("agent_invalid_output", "Invalid review message.")
        for key in ("uncertainties","alternatives"):
            if not isinstance(review[key],list) or len(review[key])>8 or any(not isinstance(v,str) or len(v)>1500 for v in review[key]):
                raise AssistantError("agent_invalid_output", "Invalid alternatives or uncertainties.")
        if not isinstance(review["concerns"],list) or len(review["concerns"])>8:
            raise AssistantError("agent_invalid_output", "Invalid concerns.")
        for item in review["concerns"]:
            if not isinstance(item,dict) or set(item)!={"code","message","refs"} or item["code"] not in ("capacity","after_due","contextual","rest_goal") or not isinstance(item["message"],str) or len(item["message"])>2000 or not isinstance(item["refs"],list) or not item["refs"] or any(not isinstance(ref,str) or ref not in inputs["evidence"] for ref in item["refs"]):
                raise AssistantError("agent_invalid_evidence", "The concern is not grounded in the supplied evidence.")
        if review["verdict"]=="review" and not review["concerns"] and not inputs["known_concerns"]:
            raise AssistantError("agent_invalid_evidence", "A decision barrier requires a concrete consequence.")
        if inputs['known_concerns'] and (review['verdict']=='allow' or not {c['code'] for c in inputs['known_concerns']}.issubset({c['code'] for c in review['concerns']})):
            raise AssistantError('agent_missed_known_concern','The model missed a verified conflict. The contextual review is incomplete; the conflict remains visible.')
        return review

    def confirm(self, identity, generation, without_review=False):
        with self.store.lock():
            if not isinstance(generation,int) or isinstance(generation,bool) or self.proposal(identity)["generation"]!=generation:
                raise AssistantError("stale_proposal", "The review you saw changed. Read the current proposal.")
            return self._admit_locked(identity,acknowledged=True,without_review=without_review)

    def _admit_locked(self, identity, acknowledged=False, without_review=False, declaration=False):
        proposal = self.proposal(identity)
        if proposal["phase"]=="applied":return proposal["result"]
        if proposal["phase"]=="admitted":
            self.app._recover()
            return self.proposal(identity)["result"]
        allowed = ("evaluating",) if declaration else ("unavailable",) if without_review else ("ready","review")
        if proposal["phase"] not in allowed:
            raise AssistantError("proposal_not_ready", "This proposal is not ready to apply.")
        inputs = self._input(proposal)
        with self.store.connect() as con:
            prior=con.execute("SELECT input FROM evaluations WHERE proposal_id=? AND generation=? AND status='completed' ORDER BY id DESC LIMIT 1",(identity,proposal['generation'])).fetchone()
        expired=bool(prior and self._review_expired(json.loads(prior[0])))
        if inputs["fingerprint"]!=proposal["fingerprint"] or expired:
            with self.store.connect() as con:con.execute("UPDATE proposals SET phase='stale',generation=generation+1 WHERE id=?",(identity,))
            raise AssistantError("stale_proposal", "Relevant context changed. Review the decision again.")
        if proposal["facts"] and not acknowledged and not declaration:
            raise AssistantError("decision_required", "A consequence requires your decision.")
        plan = inputs["plan"]
        plan.update(revision=self.store.revision(),proposal_id=identity,action=proposal["intent"]["action"],target_uuid=proposal["intent"]["payload"].get("uuid") or plan["result"].get("uuid"),undo_of=proposal["intent"]["payload"].get("undo_of"),review_complete=not without_review and not declaration,review_status='not_required' if declaration else 'incomplete' if without_review else 'complete')
        operation_key = "proposal:"+identity
        acknowledged_items = [dict(c,basis={r:inputs['evidence'][r] for r in c['refs']}) for c in proposal['facts']] if acknowledged else []
        with self.store.connect() as con:
            con.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?)",(operation_key,identity,proposal["generation"],proposal["digest"],inputs["fingerprint"],encoded(acknowledged_items),int(not without_review),self.app.now()))
            con.execute("INSERT INTO operations(key,digest,action,plan,status,created_at) VALUES(?,?,?,?, 'prepared',?)",(operation_key,proposal["digest"],proposal["intent"]["action"],encoded(plan),self.app.now()))
            con.execute("UPDATE proposals SET phase='admitted',operation_key=? WHERE id=?",(operation_key,identity))
            for item in acknowledged_items:con.execute("INSERT OR IGNORE INTO acknowledgments VALUES(?,?,?)",(item["signature"],item["code"],self.app.now()))
        self.app.fault("after_admission")
        result = self.app._apply(operation_key,plan)
        self._refresh_pending(identity)
        if proposal['intent']['action'] in ('context_update','continuity_update'):
            self._queue_context_notice()
        return result

    def _queue_context_notice(self):
        snapshot=self._snapshot()
        capacity=self._capacity(snapshot['tasks'],snapshot['context'])
        if not capacity:return
        with self.store.connect() as con:
            if con.execute('SELECT 1 FROM acknowledgments WHERE signature=?',(capacity['signature'],)).fetchone():return
            key='context-notice:'+capacity['signature']
            proposal={'id':key,'key':key,'intent':{'action':'context_update','payload':{}},'generation':0,'origin':'ui','request_text':''}
            inputs=self._input(proposal,snapshot)
            con.execute("INSERT OR IGNORE INTO messages(key,role,text,status,data,at) VALUES(?,'assistant',?,'pending',?,?)",
                (key,'Your correction was saved. The active commitments now exceed the declared window; contextual guidance is pending.',encoded({'kind':'context_notice','input':{k:v for k,v in inputs.items() if k!='plan'}}),self.app.now()))

    def evaluate_notices(self):
        with self.store.lock(),self.store.connect() as con:
            rows=[dict(r) for r in con.execute("SELECT * FROM messages WHERE role='assistant' AND status='pending' AND key LIKE 'context-notice:%'")]
            for row in rows:con.execute("UPDATE messages SET status='evaluating' WHERE key=? AND status='pending'",(row['key'],))
        for row in rows:
            inputs=json.loads(row['data'])['input']
            try:
                if self.bridge is None:raise AssistantError('agent_unavailable','The assistant is unavailable.')
                result=self.bridge.turn(self.bridge.thread(),'Give informational, empathetic guidance after a human context correction. The declaration is already saved; propose no mutation and no barrier. Ground any concern in evidence, acknowledge uncertainty about unrecorded progress, and offer a useful small next step or renegotiation.\n'+encoded(inputs),REVIEW_SCHEMA)
                output=self.validate_review(result['output'],inputs)
                text=output['message']+(' '+ ' '.join(output['alternatives']) if output['alternatives'] else '')
                status='completed';data={'kind':'context_notice','result':result,'review_status':'complete'}
            except Exception as error:
                text='Your correction was saved. Contextual guidance is incomplete: '+str(error)[:300]
                status='incomplete';data={'kind':'context_notice','error':str(error)[:300],'review_status':'incomplete'}
            with self.store.lock(),self.store.connect() as con:
                snapshot=self._snapshot()
                current=self._capacity(snapshot['tasks'],snapshot['context'])
                latest=self._input({'id':row['key'],'key':row['key'],'intent':{'action':'context_update','payload':{}},'generation':0,'origin':'ui','request_text':''},snapshot)
                if not current or row['key']!='context-notice:'+current['signature'] or latest['fingerprint']!=inputs['fingerprint'] or self._review_expired(inputs):
                    status='stale';text='Context changed while this guidance was being prepared. Consult the current commitments.'
                con.execute('UPDATE messages SET text=?,status=?,data=? WHERE key=? AND status=\'evaluating\'',(text,status,encoded(data),row['key']))

    def _refresh_pending(self, applied):
        with self.store.connect() as con:pending = [r[0] for r in con.execute("SELECT id FROM proposals WHERE id!=? AND phase IN ('evaluating','review','unavailable','stale')",(applied,))]
        for identity in pending:
            proposal = self.proposal(identity)
            try:changed = self._input(proposal)["fingerprint"]!=proposal["fingerprint"]
            except AssistantError:changed = True
            if changed:
                with self.store.connect() as con:con.execute("UPDATE proposals SET phase='stale',generation=generation+1 WHERE id=?",(identity,))
                self._cancel_inflight(identity)

    def undo(self, fact_id, key, expected_revision=None):
        with self.store.lock():
            with self.store.connect() as con:
                old=con.execute('SELECT * FROM proposals WHERE key=?',(key,)).fetchone()
            if old:
                if json.loads(old['intent'])['payload'].get('undo_of')!=fact_id:raise AssistantError('idempotency_conflict','The key belongs to a different undo.')
                return self.unpack(old)
            if expected_revision is not None and expected_revision!=self.store.revision():
                raise AssistantError('stale_revision','The state changed. Reopen the current action before undoing it.')
            with self.store.connect() as con:row=con.execute("SELECT * FROM facts WHERE id=?",(fact_id,)).fetchone()
            if not row:raise AssistantError("fact_not_found", "The fact does not exist.")
            plan=json.loads(row["plan"])
            if len(plan["steps"])!=1 or plan["effect"] or plan["steps"][0].get("before") is None:
                raise AssistantError("undo_unavailable", "Undo supports state changes to an existing responsibility.")
            step=plan["steps"][0]
            payload={"uuid":step["uuid"],"expected_after":step["after"],"patch":{k:step["before"].get(k) for k in step["patch"]},"undo_of":fact_id}
        return self.request("task_restore",payload,key,request_text="Undo fact "+str(fact_id),inverse=True)
