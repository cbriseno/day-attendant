"""Persistent proposals for an explicitly initialized personal or synthetic profile.

Taskwarrior remains the task authority. A proposal cannot be recovered as an
operation until admission and its authorization are committed together.
"""
from datetime import timedelta
from contextlib import nullcontext
import hashlib
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
    "task_add": {"title", "priority", "due", "source", "zone", "person", "reference", "observed_at", "estimate_minutes", "remainingMinutes", "awaiting", "context", "next"},
    "task_update": {"uuid", "title", "priority", "due", "zone", "progress", "context", "next", "awaiting", "remainingMinutes"},
    "front_activate": {"uuid"}, "front_todo": {"uuid"}, "front_retire": {"uuid"},
    "task_snooze": {"uuid", "until", "zone", "fold"}, "task_done": {"uuid"}, "task_reopen":{"uuid"},
    "context_update": {"availableMinutes", "availableUntil", "rest"},
    "continuity_update": {"uuid", "remainingMinutes", "awaiting", "context", "next"},
    "dependency_update": {"uuid","dependsOn"},
    "related_update": {"uuid","relatedTo"},
    "provenance_update": {"uuid","source","person","reference","observed_at","zone"},
    "event_add": {"title","start","end","zone","fold","source","person","reference","observed_at"},
    "event_update": {"uuid","title","start","end","zone","fold"},
    "event_cancel": {"uuid"},
}
PENDING = ("evaluating", "review", "unavailable", "stale", "invalid")


class Connected:
    def __init__(self, app, bridge=None):
        self.app = app
        self.store = app.store
        self.bridge = bridge
        self.inflight = {}
        self.inflight_lock = threading.Lock()

    def initialize(self,kind=None,zone=None,name=None,_upgrade_backup=None):
        """Initialize an explicitly selected empty profile, never import personal data."""
        from .profiles import new_profile,default_policy
        from .archives import recover_maintenance
        recover_maintenance(self.app)
        with self.store.lock():
            if self.store.state_value('maintenance') and self.store.state_value('profile'):return
            self.store.assert_writable()
            with self.store.connect() as con:version=con.execute('PRAGMA user_version').fetchone()[0]
            if version==3 and not _upgrade_backup:
                raise AssistantError('profile_upgrade_required','Stop the old service and run profile upgrade; a verified backup is required before migration.')
            if version==3:
                from .archives import verify,task_digest
                previous=verify(_upgrade_backup)
                if previous['state_schema']!=3 or previous['revision']!=self.store.revision() or previous['task_fingerprint']!=task_digest(self.app.tasks.rows()):raise AssistantError('migration_stale','The profile changed after its migration backup. Capture a fresh backup before upgrading.')
            existing=self.store.state_value('connected',False)
            profile=self.store.state_value('profile')
            selected=kind or (profile or {}).get('kind') or 'synthetic'
            if profile and kind and profile['kind']!=kind:
                raise AssistantError('profile_kind_conflict','A profile cannot be converted between personal and synthetic by restarting it.')
            if not existing and self.store.unresolved():
                raise AssistantError('migration_recovery_required','Recover admitted operations before initializing this profile.')
            if not existing and self.app.tasks.rows() and not self.store.state_value('synthetic_bootstrap',False):
                raise AssistantError('unmarked_state','Existing unmarked task data is not imported automatically.')
            if selected=='personal' and not profile and self.store.state_value('synthetic_bootstrap',False):raise AssistantError('profile_kind_conflict','Synthetic data is not converted into a personal profile.')
            profile=profile or new_profile(self.store.path,selected,zone or self.store.state_value('timezone'),name)
            if existing and not profile.get('model') and self.store.state_value('agent_model'):profile['model']=self.store.state_value('agent_model')
            if zone and existing and zone!=profile['timezone']:
                raise AssistantError('zone_change_required','Change the profile timezone explicitly in Settings; restart alone does not replace it.')
            with self.store.connect() as con:
                con.executescript('''BEGIN IMMEDIATE;
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

                    CREATE TABLE IF NOT EXISTS related(a TEXT NOT NULL,b TEXT NOT NULL,PRIMARY KEY(a,b),CHECK(a<b));
                    CREATE TABLE IF NOT EXISTS attachments(id TEXT PRIMARY KEY,name TEXT NOT NULL,mime TEXT NOT NULL,sha256 TEXT NOT NULL,relative_path TEXT NOT NULL,created_at TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS attachment_requests(key TEXT PRIMARY KEY,sha256 TEXT NOT NULL,attachment_id TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS message_attachments(message_key TEXT NOT NULL,attachment_id TEXT NOT NULL,PRIMARY KEY(message_key,attachment_id));
                    CREATE TABLE IF NOT EXISTS reconciliations(id INTEGER PRIMARY KEY AUTOINCREMENT,operation_key TEXT NOT NULL,resolution TEXT NOT NULL,observed TEXT NOT NULL,backup TEXT NOT NULL,at TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS settings_changes(key TEXT PRIMARY KEY,digest TEXT NOT NULL,changes TEXT NOT NULL,result TEXT NOT NULL,at TEXT NOT NULL);
                ''')
                if 'generation' not in {r[1] for r in con.execute('PRAGMA table_info(messages)')}:
                    con.execute('ALTER TABLE messages ADD COLUMN generation INTEGER NOT NULL DEFAULT 0')
                if 'capture_only' not in {r[1] for r in con.execute('PRAGMA table_info(messages)')}:
                    con.execute('ALTER TABLE messages ADD COLUMN capture_only INTEGER NOT NULL DEFAULT 0')
                if 'message_key' not in {r[1] for r in con.execute('PRAGMA table_info(proposals)')}:
                    con.execute('ALTER TABLE proposals ADD COLUMN message_key TEXT')
                    for m in con.execute("SELECT key FROM messages WHERE role='user'").fetchall():con.execute("UPDATE proposals SET message_key=? WHERE key=? AND origin='chat'",(m[0],'chat-action:'+m[0]))
                # Unknown progress is nullable; partial native capture must not
                # manufacture a human activity timestamp to repair provenance.
                if any(r[1]=='human_progress_at' and r[3] for r in con.execute('PRAGMA table_info(task_meta)')):
                    con.execute('ALTER TABLE task_meta RENAME TO task_meta_before_v4')
                    con.execute('CREATE TABLE task_meta(uuid TEXT PRIMARY KEY,source TEXT,person TEXT,reference TEXT,observed_at TEXT,captured_at TEXT NOT NULL,human_progress_at TEXT,origin_zone TEXT,estimate_minutes INTEGER)')
                    con.execute('INSERT INTO task_meta SELECT * FROM task_meta_before_v4')
                    con.execute('DROP TABLE task_meta_before_v4')
                con.execute('CREATE TABLE IF NOT EXISTS item_attachments(entity_uuid TEXT NOT NULL,attachment_id TEXT NOT NULL,PRIMARY KEY(entity_uuid,attachment_id))')
                columns={r[1] for r in con.execute('PRAGMA table_info(events)')}
                for column in ('source','person','reference','observed_at','captured_at'):
                    if column not in columns:con.execute('ALTER TABLE events ADD COLUMN '+column+' TEXT')
                con.execute("INSERT OR IGNORE INTO state VALUES('profile',?)",(encoded(profile),))
                con.execute("INSERT OR IGNORE INTO state VALUES('timezone',?)",(encoded(profile['timezone']),))
                con.execute("INSERT OR IGNORE INTO state VALUES('notification_policy',?)",(encoded(default_policy()),))
                con.execute("INSERT OR IGNORE INTO state VALUES('context_epoch','0')")
                con.execute("INSERT OR IGNORE INTO state VALUES('synthetic_bootstrap',?)",(encoded(selected=='synthetic'),))
                if selected=='synthetic':con.execute("INSERT OR IGNORE INTO state VALUES('bootstrap_clock',?)",(encoded(self.app.now()),))
                con.execute('PRAGMA user_version=4')
            if existing:return
            if selected=='personal':
                with self.store.connect() as con:con.execute("INSERT OR REPLACE INTO state VALUES('connected','true')")
                return
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
        with self.store.lock(),self.store.connect() as con:
            con.execute("INSERT OR REPLACE INTO state VALUES('connected','true')")

    def startup(self):
        with self.store.lock():
            if self.store.state_value('maintenance'):return
            unresolved = self.app._recover()
            # Conflicts remain visible in a read-only diagnostic workspace.
            with self.store.connect() as con:
                con.execute("UPDATE proposals SET phase='unavailable',generation=generation+1,error=? WHERE phase IN ('evaluating','ready')",
                            ("The review was interrupted by a restart. Retry it or make an explicit decision.",))
                con.execute("UPDATE evaluations SET status='interrupted' WHERE status='running'")
                pending_messages=[dict(r) for r in con.execute("SELECT * FROM messages WHERE role='user' AND status='pending'")]
                for message in pending_messages:
                    con.execute("INSERT OR IGNORE INTO messages(key,role,text,status,data,at) VALUES(?,'assistant',?,'incomplete',?,?)",
                        (message['key']+':assistant','The response was interrupted by a restart. Retry it explicitly; any existing action retains its own state.',encoded({'code':'restart_interrupted'}),self.app.now()))
                con.execute("UPDATE messages SET status='incomplete',generation=generation+1 WHERE status IN ('pending','evaluating')")

    def require_agent(self):
        if self.bridge is None or not getattr(self.bridge,'available',True):
            raise AssistantError('agent_unavailable','The assistant is unavailable. Known-data reminders still work.')
        profile=self.store.state_value('profile',{})
        provider=getattr(self.bridge,'provider',profile.get('harness','codex'))
        effort=getattr(self.bridge,'effort',None)
        if provider!=profile.get('harness','codex') or profile.get('model') and self.bridge.model and profile['model']!=self.bridge.model or profile.get('effort') and effort and profile['effort']!=effort:
            raise AssistantError('agent_restart_required','Apply the selected harness/model before sending another model request.')
        return self.bridge

    def pin_model(self,result,epoch=None):
        with self.store.lock(),self.store.connect() as con:
            if self.store.state_value('maintenance') or epoch is not None and self.store.epoch()!=epoch:return
            profile=self.store.state_value('profile',{})
            provider=result.get('provider') or getattr(self.bridge,'provider',profile.get('harness'))
            if provider==profile.get('harness') and not profile.get('model'):
                profile['model']=result['model']
                con.execute("INSERT OR REPLACE INTO state VALUES('profile',?)",(encoded(profile),))

    def message_images(self,key):
        if not key:return []
        with self.store.connect() as con:
            rows=[dict(r) for r in con.execute('SELECT a.* FROM attachments a JOIN message_attachments m ON m.attachment_id=a.id WHERE m.message_key=? ORDER BY a.id',(key,))]
        result=[];base=(self.store.path/'attachments').resolve()
        for row in rows:
            path=(self.store.path/row['relative_path']).resolve()
            if not path.is_relative_to(base) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
                raise AssistantError('attachment_corrupt','A selected image is missing or failed its integrity check.')
            result.append(dict(row,path=str(path)))
        return result

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
        unresolved=self.store.unresolved()
        maintenance=self.store.state_value('maintenance')
        rows = self.app._view(coherent=not bool(unresolved or maintenance))
        with self.store.connect() as con:
            continuation = {r["uuid"]: json.loads(r["data"]) for r in con.execute("SELECT * FROM continuity")}
            proposals = [self.unpack(r) for r in con.execute("SELECT * FROM proposals ORDER BY rowid")]
            facts = [dict(r) for r in con.execute("SELECT * FROM facts ORDER BY id")]
            # Only the current conversation is shown and sent; /clear keeps older messages stored locally.
            conversation=self.store.state_value('conversation') or {}
            messages = [dict(r) for r in con.execute("SELECT * FROM messages WHERE id>? ORDER BY id",(conversation.get('floor',0),))]
            transport_issues=[dict(r) for r in con.execute("SELECT id,entity,status,error,retry_at FROM notices WHERE status IN ('failed','uncertain') OR status='pending' AND error IS NOT NULL")]
            related=[dict(r) for r in con.execute('SELECT * FROM related ORDER BY a,b')]
            image_links=[dict(r) for r in con.execute('SELECT * FROM item_attachments')]
            selected_images={r['message_key']:[] for r in con.execute('SELECT DISTINCT message_key FROM message_attachments')}
            for r in con.execute('SELECT m.message_key,a.id,a.name,a.mime FROM message_attachments m JOIN attachments a ON a.id=m.attachment_id ORDER BY a.id'):selected_images[r['message_key']].append({'id':r['id'],'name':r['name'],'mime':r['mime']})
            for message in messages:message['attachments']=selected_images.get(message['key'],[])
        startup=self.store.state_value('agent_startup_error') or {}
        tasks = []
        for row in rows:
            extra = continuation.get(row["uuid"], {})
            state = "done" if row["status"] == "completed" else "retired" if row["status"] == "deleted" else "snoozed" if row["status"] == "waiting" else "active" if row["focused"] else "todo"
            tasks.append({"id": row["uuid"], "title": row["title"], "state": state,
                "priority": row["priority"], "due": row["due_at"], "until": row["snoozed_until"],
                "blocked": bool(row['blockedBy']), "dependsOn":row['dependsOn'],"blockedBy":row['blockedBy'],
                "relatedTo":[r['b'] if r['a']==row['uuid'] else r['a'] for r in related if row['uuid'] in (r['a'],r['b'])],
                "attachmentIds":[r['attachment_id'] for r in image_links if r['entity_uuid']==row['uuid']],
                "source":row['metadata'].get('source','general'),"person":row['metadata'].get('person'),"reference":row['metadata'].get('reference'),
                "observedAt":row['metadata'].get('observed_at'),"capturedAt":row['metadata'].get('captured_at'),
                "context": extra.get("context", ""), "next": extra.get("next", ""),
                "remainingMinutes": extra.get("remainingMinutes"), "awaiting": extra.get("awaiting", False),
                "progressRecordedAt": row["metadata"].get("human_progress_at")})
        return {"tasks": tasks, "context": self.store.state_value("day_context", {"availableMinutes": None, "availableUntil": None, "rest": False}),
                "timezone": self.store.state_value('timezone','America/Edmonton'),
                "calendar_events":self.store.events(),"notification_policy":self.store.state_value('notification_policy'),
                "proposals": proposals, "events": facts, "messages": messages,
                "revision": self.store.revision(), "now": self.app.now(), "synthetic": self.store.state_value('profile',{}).get('kind')=='synthetic',
                "profile":self.store.state_value('profile'), "readOnly":bool(unresolved or maintenance),
                "diagnostics":{"operations":unresolved,"maintenance":maintenance,"reminders":transport_issues}, "epoch":self.store.epoch(),
                "conversation":{'clearedAt':conversation.get('clearedAt')},
                "operations_status":{"last_tick":self.store.state_value('last_tick'),'last_backup':self.store.state_value('last_backup'),'scheduler':self.store.state_value('scheduler')},
                "agent": {"provider":self.store.state_value('profile',{}).get('harness'),'connected':  self.bridge is not None and getattr(self.bridge,'available',True) and getattr(self.bridge,'provider',self.store.state_value('profile',{}).get('harness'))==self.store.state_value('profile',{}).get('harness'),
                          "model": self.bridge.model if self.bridge else self.store.state_value('profile',{}).get('model'),
                          "effort": getattr(self.bridge,'effort',None) if self.bridge else self.store.state_value('profile',{}).get('effort'),
                          "catalog": getattr(self.bridge,'catalog',None) if self.bridge else None,
                          "error":startup.get('message'),"errorCode":startup.get('code')}}

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
        active = [t for t in tasks if t["state"] == "active" and not t["awaiting"] and not t.get('blocked') and t["due"] and t["due"] <= until and t["remainingMinutes"] is not None]
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
            if action == "task_reopen":target.update(state="todo",until=None)
            if action == "front_retire": target.update(state="retired", until=None)
            if action in ("task_update", "task_restore", "dependency_update"):
                after = plan["steps"][0]["after"] if plan["steps"] else None
                if after:
                    target.update(title=after["description"], due=stamp(parse(after["due"])) if after.get("due") else None,
                        until=stamp(parse(after["wait"])) if after.get("wait") else None,
                        state="done" if after["status"]=="completed" else "retired" if after["status"]=="deleted" else "snoozed" if after.get("wait") and parse(after["wait"])>self.app.clock() else "active" if after.get("start") else "todo")
                    target['dependsOn']=after.get('depends',[])
                    target['blockedBy']=[uid for uid in target['dependsOn'] if next((t['state'] for t in proposed if t['id']==uid),None)!='done']
                    target['blocked']=bool(target['blockedBy'])
        if target and 'continuity_set' in plan['effect']:target.update(plan['effect']['continuity_set'][1])
        evidence = {"context.rest": snapshot["context"].get("rest", False)}
        for t in proposed:
            t['blockedBy']=[uid for uid in t['dependsOn'] if next((x['state'] for x in proposed if x['id']==uid),None)!='done'] if t['state'] not in ('done','retired') else []
            t['blocked']=bool(t['blockedBy'])
        for k in ("availableMinutes", "availableUntil"):
            if snapshot["context"].get(k) is not None: evidence["context."+k] = snapshot["context"][k]
        for t in snapshot["tasks"]:
            if t["id"] == target_id or t["state"] == "active":
                for k in ("title", "state", "due", "until", "remainingMinutes", "awaiting", "context", "next", "progressRecordedAt"):
                    if t.get(k) is not None: evidence["tasks."+t["id"]+"."+k] = t[k]
        concerns = []
        graph_neighbors=[]
        if target_id:
            prerequisites=set((current or {}).get('dependsOn',[]))|set((target or {}).get('dependsOn',[]))
            graph_neighbors=[t for t in snapshot['tasks'] if t['id'] in prerequisites or target_id in t['dependsOn']]
            evidence['relationships.prerequisites']=[{'id':t['id'],'title':t['title'],'state':t['state']} for t in graph_neighbors if t['id'] in prerequisites]
            dependents=[t for t in graph_neighbors if target_id in t['dependsOn'] and t['state'] not in ('done','retired')]
            evidence['relationships.dependents']=[{'id':t['id'],'title':t['title'],'state':t['state']} for t in dependents]
            for related in graph_neighbors:
                for field in ('title','state','dependsOn','blockedBy','due','remainingMinutes','awaiting'):
                    if related.get(field) is not None:evidence['tasks.'+related['id']+'.'+field]=related[field]
            if target and dependents and (action=='front_retire' or action in ('task_restore','task_reopen') and current['state']=='done' and target['state']!='done'):
                evidence['proposal.state']=target['state']
                concerns.append({'code':'contextual','message':'This responsibility remains a prerequisite for other work. Retiring it does not fulfill that prerequisite; reopening it blocks dependent work again. Dependents are not started or completed automatically.',
                    'message_key':'risk_dependency_state','message_params':{},'refs':['relationships.dependents','proposal.state'],
                    'signature':fingerprint(['prerequisite-state',target_id,target['state'],evidence['relationships.dependents']])})
            if action=='dependency_update' and target and target['state']=='active' and target['blocked']:
                evidence['proposal.blockedBy']=target['blockedBy']
                concerns.append({'code':'contextual','message':'This active front will have unfinished prerequisites. It stays tracked as active, but cannot be executed as unblocked work until those prerequisites are complete.',
                    'message_key':'risk_active_blocked','message_params':{},'refs':['proposal.blockedBy'],
                    'signature':fingerprint(['active-blocked',target_id,target['blockedBy']])})
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
        horizon=max([parse(snapshot['context'].get('availableUntil') or self.app.now())]+[parse(t[k]) for t in snapshot['tasks'] if t['id']==target_id or t['state']=='active' for k in ('due','until') if t.get(k)])
        horizon=max(horizon,self.app.clock()+timedelta(days=1))
        for field in ('due','until','start','end'):
            if intent['payload'].get(field):horizon=max(horizon,parse(instant(intent['payload'][field],intent['payload'].get('zone'),intent['payload'].get('fold'))))
        calendar=[e for e in snapshot.get('calendar_events',[]) if e['uuid']==target_id or parse(e['end_at'])>self.app.clock() and parse(e['start_at'])<=horizon]
        evidence['calendar_events']=calendar
        if action=='task_snooze' and target:
            clashes=[e for e in calendar if parse(e['start_at'])<=parse(target['until'])<parse(e['end_at'])]
            if clashes:
                evidence['calendar.resume_conflicts']=clashes
                concerns.append({'code':'contextual','message':'The selected resume time falls during a registered calendar commitment. You may prefer a reminder outside that commitment.',
                    'message_key':'risk_calendar_resume','message_params':{},'refs':['calendar.resume_conflicts','proposal.until'],
                    'signature':fingerprint(['calendar-resume',target_id,target['until'],clashes])})
        # A different unrelated todo/completed item doesn't invalidate this review.
        relevant = {"epoch":self.store.epoch(), "timezone":snapshot.get("timezone"), "target": current, "intent": intent, "context": snapshot["context"],
                    "graph_neighbors":graph_neighbors,"calendar_events":calendar,
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
            "tasks": [t for t in snapshot["tasks"] if t["id"]==target_id or t["state"]=="active" or t in graph_neighbors],
            "calendar_events":calendar,
            "human_context":[{'role':m['role'],'text':m['text'],'status':m['status']} for m in snapshot['messages'][-12:] if m['status']=='completed'],
            "proposed_task":target, "context":snapshot["context"], "evidence":evidence,
            "known_concerns":[c for c in concerns if c['signature'] not in acknowledged], "capacityAlreadyReviewed":already,
            "capacity_signature":capacity and capacity['signature'],
            "acknowledged_consequences":self._acknowledged_context(evidence,acknowledged),
            "epoch":snapshot["epoch"],"fingerprint":fingerprint(relevant), "plan":plan}

    def _acknowledged_context(self, evidence, signatures):
        with self.store.connect() as con:
            items=[item for row in con.execute('SELECT acknowledged FROM decisions') for item in json.loads(row[0])]
        result={}
        for item in items:
            refs=item.get('refs',[])
            if item['signature'] in signatures and item.get('basis')=={r:evidence.get(r) for r in refs}:
                result[item['signature']]=item
        return list(result.values())

    def request(self, action, payload, key, origin="ui", request_text="", expected_revision=None, inverse=False, message_guard=None, connection=None, validate_only=False, canceled_after_commit=None, _lock_held=False):
        if not isinstance(key, str) or not 1 <= len(key) <= 200 or not isinstance(payload, dict):
            raise AssistantError("invalid_request", "The request requires content and a stable key.")
        if not isinstance(action,str) or action != "task_restore" and (action not in ALLOWED or set(payload)-ALLOWED[action]):
            raise AssistantError("unsupported_action", "Unsupported action or fields.")
        for name in ('uuid','title','priority','due','until','start','end','observed_at','zone','source','person','reference','context','next'):
            if name in payload and payload[name] is not None and not isinstance(payload[name],str):
                raise AssistantError('invalid_request','Text fields must contain strings.')
        if payload.get('priority') is not None and payload['priority'] not in ('high','medium','low'):
            raise AssistantError('invalid_request','Invalid priority.')
        if 'source' in payload and payload['source'] not in ('general','ticket','pr','chat','email','call','research'):
            raise AssistantError('invalid_source','Choose a supported capture source.')
        if any(len(payload[k])>2000 for k in ('person','reference') if payload.get(k)):
            raise AssistantError('invalid_provenance','Provenance text is too long.')
        if payload.get('observed_at'):
            observed=instant(payload['observed_at'],payload.get('zone') or self.store.state_value('timezone'),payload.get('fold'))
            if parse(observed)>self.app.clock():raise AssistantError('future_observation','Observation time cannot be in the future.')
        if 'estimate_minutes' in payload and (type(payload['estimate_minutes']) is not int or not 0<payload['estimate_minutes']<=100000):
            raise AssistantError('invalid_estimate','Initial estimates must be positive integer minutes.')
        if action=='task_add' and (not isinstance(payload.get('title'),str) or not payload['title'].strip()):
            raise AssistantError('invalid_request','A capture requires an explicit title.')
        if 'progress' in payload and not isinstance(payload['progress'],bool):
            raise AssistantError('invalid_request','Progress requires an explicit declaration.')
        if action == "task_restore" and not inverse:
            raise AssistantError("invalid_restore", "Use Undo on a confirmed fact.")
        intent = {"action":action,"payload":payload}
        digest = fingerprint(intent)
        with (self.store.lock() if connection is None and not _lock_held else nullcontext()):
            if message_guard:
                with (self.store.connect() if connection is None else nullcontext(connection)) as con:
                    message=con.execute('SELECT status,generation FROM messages WHERE key=?',(message_guard[0],)).fetchone()
                if not message or message['status']!='pending' or message['generation']!=message_guard[1]:
                    raise AssistantError('stale_message','This conversation attempt is no longer current.')
            with (self.store.connect() if connection is None else nullcontext(connection)) as con:
                old = con.execute("SELECT * FROM proposals WHERE key=?", (key,)).fetchone()
            if old:
                if old["digest"] != digest: raise AssistantError("idempotency_conflict", "This key identifies a different intent.")
                return self.unpack(old)
            self.store.assert_writable()
            self.app.clean_read()
            if expected_revision is not None and expected_revision != self.store.revision():
                raise AssistantError("stale_revision", "Read the current state before changing it.")
            plan = self._plan(intent, key)  # Validate before superseding any prior intent.
            identity = str(uuid.uuid4())
            prefix='calendar:' if action.startswith('event_') else 'dependencies:' if action=='dependency_update' else 'related:' if action=='related_update' else 'metadata:' if action in ('continuity_update','provenance_update') else 'task:'
            entity=prefix+payload['uuid'] if payload.get('uuid') else 'context' if action=='context_update' else 'new:'+key
            proposal = {"id":identity,"key":key,"intent":intent,"generation":0,"request_text":request_text,"origin":origin}
            inputs = self._input(proposal)
            if validate_only:return {'intent':intent,'validated':True}
            phase = "evaluating" if plan["steps"] or plan["effect"] else "unchanged"
            with (self.store.connect() if connection is None else nullcontext(connection)) as con:
                superseded = [r[0] for r in con.execute("SELECT id FROM proposals WHERE entity=? AND phase IN ('evaluating','review','unavailable','stale','invalid') AND (message_key IS NULL OR message_key!=?)",(entity,message_guard[0] if message_guard else ''))]
                con.execute("UPDATE proposals SET phase='superseded',generation=generation+1 WHERE entity=? AND phase IN ('evaluating','review','unavailable','stale','invalid') AND (message_key IS NULL OR message_key!=?)",(entity,message_guard[0] if message_guard else ''))
                con.execute("INSERT INTO proposals(id,key,digest,intent,entity,origin,request_text,phase,generation,fingerprint,facts,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identity,key,digest,encoded(intent),entity,origin,request_text,phase,0,inputs["fingerprint"],encoded(inputs["known_concerns"]),self.app.now()))
                if message_guard:con.execute('UPDATE proposals SET message_key=? WHERE id=?',(message_guard[0],identity))
                if phase=='unchanged' and superseded:con.execute("UPDATE proposals SET result=? WHERE id=?",(encoded({'superseded':superseded}),identity))
            for old_id in superseded:
                if canceled_after_commit is not None:canceled_after_commit.append(old_id)
                else:self._cancel_inflight(old_id)
            if phase=="evaluating" and action in ("context_update","continuity_update","provenance_update","related_update") and origin in ('ui','cli'):
                self._admit_locked(identity, acknowledged=False, declaration=True)
            if connection is not None:return self.unpack(connection.execute('SELECT * FROM proposals WHERE id=?',(identity,)).fetchone())
            return self.proposal(identity)

    def request_many(self,plans,key,text,revision,generation):
        canceled=[]
        with self.store.lock():
            # Validate every field/participant before superseding any prior intent.
            for index,(action,payload) in enumerate(plans):
                suffix='' if index==0 else ':'+str(index)
                self.request(action,payload,'chat-action:'+key+suffix,origin='chat',request_text=text,
                    expected_revision=revision,message_guard=(key,generation),connection=None,
                    validate_only=True,_lock_held=True)
            with self.store.connect() as con:
                result=[self.request(action,payload,'chat-action:'+key+('' if index==0 else ':'+str(index)),
                    origin='chat',request_text=text,expected_revision=revision,message_guard=(key,generation),
                    connection=con,canceled_after_commit=canceled) for index,(action,payload) in enumerate(plans)]
        for identity in canceled:self._cancel_inflight(identity)
        return result

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
            self.store.assert_writable();self.app.clean_read()
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
            self.store.assert_writable();self.app.clean_read()
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
            bridge=self.require_agent()
            thread=bridge.thread()
            prompt = """Review this pending intent before application; execute nothing. Ground consequences in concrete evidence, never in a universal active-front count. front_activate marks a responsibility as an active front for tracking and later alternation; it does NOT assert that its next step is executable now or that a reply arrived. awaiting=true alone is a legitimate active front and does not justify a barrier. Native blocking dependencies are checked by the service separately.
For authorization_source=authenticated_typed_request, the exact typed UI/CLI action is already the authenticated operator's explicit instruction. No second prose instruction is required. For interpreted_human_message, verify EVERY proposed field against the exact current human message, selected image content, and clearly relevant prior human requests in human_context. Assistant suggestions do not authorize themselves. A current clarification may complete a specific prior human request, but vague acceptance without unambiguous scope is insufficient. A quoted substring and userAuthorized are not sufficient proof of scope; extra fields, negated changes or ambiguity require insufficient and a concrete clarification. This also applies to context/continuity declarations. service_metadata.zone is the existing workspace timezone used to interpret an explicitly requested date, not a separate task-field edit requiring a second instruction.
Authorizing an intent never acknowledges its consequences. known_concerns are detected, UNACCEPTED conflicts. Address every known code and its exact evidence refs with verdict=review, never allow. acknowledged_consequences are specific accepted bases, not category-wide permission. capacityAlreadyReviewed refers ONLY to the same aggregate capacity_signature. Never suppress a distinct task or completion-window concern because it shares a category.
Respect legitimate rest, unrecorded progress, uncertainty and autonomy. Allow unambiguous captures and requests without a supported consequence, without routine confirmation. Use exact refs from evidence. Do not invent facts. Respond in the current human message's language; default to English for typed actions with no prose request.
"""+encoded({k:v for k,v in inputs.items() if k!='plan'})
            images=self.message_images(proposal.get('message_key'))
            kwargs={'images':images} if images else {}
            result=bridge.turn(thread,prompt,REVIEW_SCHEMA,canceled=canceled,**kwargs)
            result=dict(result,provider=getattr(bridge,'provider',self.store.state_value('profile',{}).get('harness')))
            self.pin_model(result,inputs['epoch'])
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
                if proposal["phase"] in ('evaluating','ready') and proposal["generation"]==generation:con.execute("UPDATE proposals SET phase='unavailable',error=? WHERE id=?",(str(error)[:400],identity))
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
            self.store.assert_writable()
            unresolved=self.app._recover()
            current=self.proposal(identity)
            if current['phase']=='applied':return current['result']
            raise AssistantError('recovery_required','The admitted operation requires reconciliation; inspect its durable operation.',{'operations':unresolved})
        allowed = ("evaluating",) if declaration else ("unavailable",) if without_review else ("ready","review")
        if proposal["phase"] not in allowed:
            raise AssistantError("proposal_not_ready", "This proposal is not ready to apply.")
        self.store.assert_writable();self.app.clean_read()
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
        if proposal.get('message_key'):
            images=self.message_images(proposal['message_key'])
            if images and plan['target_uuid']:plan['effect']['attachments_link']=[plan['target_uuid'],[i['id'] for i in images]]
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
            self.store.assert_writable();self.app.clean_read()
            rows=[dict(r) for r in con.execute("SELECT * FROM messages WHERE role='assistant' AND status='pending' AND key LIKE 'context-notice:%'")]
            for row in rows:con.execute("UPDATE messages SET status='evaluating' WHERE key=? AND status='pending'",(row['key'],))
        for row in rows:
            inputs=json.loads(row['data'])['input']
            try:
                with self.store.lock():
                    self.store.assert_writable();bridge=self.require_agent();epoch=self.store.epoch()
                result=bridge.turn(bridge.thread(),'Give informational, empathetic guidance after a human context correction. The declaration is already saved; propose no mutation and no barrier. Ground any concern in evidence, acknowledge uncertainty about unrecorded progress, and offer a useful small next step or renegotiation.\n'+encoded(inputs),REVIEW_SCHEMA)
                result=dict(result,provider=getattr(bridge,'provider',self.store.state_value('profile',{}).get('harness')))
                self.pin_model(result,epoch)
                output=self.validate_review(result['output'],inputs)
                text=output['message']+(' '+ ' '.join(output['alternatives']) if output['alternatives'] else '')
                status='completed';data={'kind':'context_notice','result':result,'review_status':'complete','epoch':epoch}
            except Exception as error:
                text='Your correction was saved. Contextual guidance is incomplete: '+str(error)[:300]
                status='incomplete';data={'kind':'context_notice','error':str(error)[:300],'review_status':'incomplete'}
            with self.store.lock(),self.store.connect() as con:
                snapshot=self._snapshot()
                current=self._capacity(snapshot['tasks'],snapshot['context'])
                latest=self._input({'id':row['key'],'key':row['key'],'intent':{'action':'context_update','payload':{}},'generation':0,'origin':'ui','request_text':''},snapshot)
                if self.store.state_value('maintenance') or self.store.epoch()!=inputs.get('epoch',self.store.epoch()) or not current or row['key']!='context-notice:'+current['signature'] or latest['fingerprint']!=inputs['fingerprint'] or self._review_expired(inputs):
                    status='stale';text='Context changed while this guidance was being prepared. Consult the current commitments.'
                con.execute('UPDATE messages SET text=?,status=?,data=? WHERE key=? AND status=\'evaluating\'',(text,status,encoded(data),row['key']))

    def _refresh_pending(self, applied):
        with self.store.connect() as con:pending = [r[0] for r in con.execute("SELECT id FROM proposals WHERE id!=? AND phase IN ('evaluating','review','unavailable','stale')",(applied,))]
        for identity in pending:
            proposal = self.proposal(identity)
            try:changed = self._input(proposal)["fingerprint"]!=proposal["fingerprint"]
            except AssistantError:changed = True
            if changed:
                with self.store.connect() as con:con.execute("UPDATE proposals SET phase='stale',generation=generation+1,error=? WHERE id=?",('Changed by admitted proposal:'+applied,identity))
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
            if len(plan["steps"])!=1 or any(k!="attachments_link" for k in plan["effect"]) or plan["steps"][0].get("before") is None:
                raise AssistantError("undo_unavailable", "Undo supports state changes to an existing responsibility.")
            step=plan["steps"][0]
            payload={"uuid":step["uuid"],"expected_after":step["after"],"patch":{k:step["before"].get(k) for k in step["patch"]},"undo_of":fact_id}
        return self.request("task_restore",payload,key,request_text="Undo fact "+str(fact_id),inverse=True)
