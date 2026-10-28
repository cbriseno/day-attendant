"""Coordinated task operations, focus, agenda and deterministic reminders."""
from datetime import timedelta
import hashlib
import json
import math
import uuid

from .errors import AssistantError
from . import platforms
from .store import Store, DEFAULTS
from .taskwarrior import Taskwarrior, canonical
from .timeutil import instant, parse, stamp, utcnow, wire

PRIORITIES = {"high": "H", "medium": "M", "low": "L"}
PUBLIC_PRIORITY = {v: k for k, v in PRIORITIES.items()}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def continuity_values(payload):
    value={k:payload[k] for k in ('remainingMinutes','awaiting','context','next') if k in payload}
    remaining=value.get('remainingMinutes')
    if remaining is not None and (isinstance(remaining,bool) or not isinstance(remaining,(int,float)) or not math.isfinite(remaining) or not 0<=remaining<=100000):
        raise AssistantError('invalid_estimate','Use nonnegative minutes or leave the estimate empty.')
    if 'awaiting' in value and not isinstance(value['awaiting'],bool):
        raise AssistantError('invalid_continuity','Waiting requires an explicit declaration.')
    if any(not isinstance(value[k],str) or len(value[k])>2000 for k in ('context','next') if k in value):
        raise AssistantError('invalid_continuity','Context must be short text.')
    return value


class Assistant:
    def __init__(self, state, task_bin=None, clock=utcnow, notifier=platforms.notify, fault=None):
        self.store = Store(state)
        self.tasks = Taskwarrior(self.store, task_bin)
        self.clock = clock
        self.notifier = notifier
        self.fault = fault or (lambda point: None)

    def now(self):
        return stamp(self.clock())

    @staticmethod
    def validate_uuid(uid):
        try:
            return str(uuid.UUID(uid))
        except (ValueError, AttributeError) as error:
            raise AssistantError("invalid_uuid", "Use a permanent task UUID.") from error

    def required(self, uid):
        uid = self.validate_uuid(uid)
        row = self.tasks.get(uid)
        if row is None:
            raise AssistantError("task_not_found", "The task does not exist.", {"uuid": uid})
        return row

    def actionable(self, row, by_id):
        return (row is not None and row["status"] == "pending"
                and not (row.get("wait") and parse(row["wait"]) > self.clock())
                and not self.blockers(row,by_id))

    def blockers(self,row,by_id):
        connected=self.store.state_value('connected',False)
        if connected:return [dep for dep in row.get('depends',[]) if by_id.get(dep,{}).get('status')!='completed']
        return [dep for dep in row.get('depends',[]) if by_id.get(dep,{}).get('status') in ('pending','recurring')]

    def validate_dependencies(self,target,refs):
        if not isinstance(refs,list) or len(refs)>100 or any(not isinstance(x,str) for x in refs):
            raise AssistantError('invalid_dependencies','Dependencies must be a bounded list of task UUIDs.')
        wanted=sorted({self.validate_uuid(x) for x in refs})
        rows={r['uuid']:r for r in self.tasks.rows()}
        if target in wanted:raise AssistantError('dependency_cycle','A responsibility cannot depend on itself.')
        if any(x not in rows for x in wanted):raise AssistantError('task_not_found','A selected dependency does not exist.')
        graph={uid:list(r.get('depends',[])) for uid,r in rows.items()};graph[target]=wanted
        visited=set();active=set()
        for root in graph:
            stack=[(root,False)]
            while stack:
                uid,leaving=stack.pop()
                if leaving:active.discard(uid);visited.add(uid);continue
                if uid in active:raise AssistantError('dependency_cycle','Blocking dependencies cannot form a cycle.')
                if uid in visited:continue
                if uid not in graph:raise AssistantError('invalid_dependencies','A dependency refers to a missing responsibility.')
                active.add(uid);stack.append((uid,True))
                stack.extend((other,False) for other in graph[uid])
        return wanted

    def clean_read(self):
        pending = self.store.unresolved()
        if pending:
            raise AssistantError("recovery_required", "Run recover before reading a coherent state.", {"operations": pending})

    def _recover(self):
        self.store.assert_writable()
        with self.store.connect() as con:
            rows = [dict(r) for r in con.execute("SELECT * FROM operations WHERE status IN ('prepared','applying') ORDER BY rowid")]
        for row in rows:
            try:
                self._apply(row["key"], json.loads(row["plan"]))
            except AssistantError:
                break
        return self.store.unresolved()

    def recover(self):
        with self.store.lock():
            return {"unresolved": self._recover(), "revision": self.store.revision()}

    def mutate(self, action, payload, key, expected_revision=None):
        if not key or len(key) > 200:
            raise AssistantError("key_required", "A nonempty idempotency key of at most 200 characters is required.")
        digest = fingerprint({"action": action, "payload": payload, "expected_revision": expected_revision})
        with self.store.lock():
            if self.store.state_value("connected", False):
                raise AssistantError("proposal_required", "This connected workspace requires proposals: use board request/confirm or its UI.")
            with self.store.connect() as con:
                old = con.execute("SELECT * FROM operations WHERE key=?", (key,)).fetchone()
            if old and old["digest"] != digest:
                raise AssistantError("idempotency_conflict", "This key was used for different input.")
            unresolved = self._recover()
            if unresolved:
                raise AssistantError("recovery_required", "An operation needs reconciliation.", {"operations": unresolved})
            if old:
                with self.store.connect() as con:
                    return json.loads(con.execute("SELECT result FROM operations WHERE key=?", (key,)).fetchone()[0])
            revision = self.store.revision()
            if expected_revision is not None and expected_revision != revision:
                raise AssistantError("stale_revision", "The state changed; read it before retrying.", {"revision": revision})
            if action.startswith("focus_") and expected_revision is None:
                raise AssistantError("revision_required", "Focus operations require the last observed revision.")
            plan = self._plan(action, payload, key)
            plan["revision"] = revision
            with self.store.connect() as con:
                con.execute("INSERT INTO operations(key,digest,action,plan,status,created_at) VALUES(?,?,?,?,?,?)",
                            (key, digest, action, encoded(plan), "prepared", self.now()))
            self.fault("after_intent")
            return self._apply(key, plan)

    def _plan(self, action, p, key):
        now = self.now()
        plan = {"steps": [], "effect": {}, "result": {}}

        def patch(row, changes):
            before = canonical(row)
            after = dict(before)
            for field, value in changes.items():
                if value is None:
                    after.pop(field, None)
                else:
                    after[field] = value
            if after != before:
                plan["steps"].append({"uuid": row["uuid"], "before": before, "after": after, "patch": changes})

        if action == "task_add":
            title = p["title"].strip()
            if not title or len(title) > 500:
                raise AssistantError("invalid_title", "A task needs a title of 1–500 characters.")
            if p.get("estimate_minutes") is not None and p["estimate_minutes"] <= 0:
                raise AssistantError("invalid_estimate", "Estimate must be positive.")
            uid = str(uuid.uuid4())
            row = {"uuid": uid, "description": title, "status": "pending", "entry": wire(now),
                   "priority": PRIORITIES[p.get("priority", "medium")]}
            if p.get("due"):
                row["due"] = wire(instant(p["due"], p.get("zone"), p.get("fold")))
            observed = instant(p["observed_at"], p.get("zone"), p.get("fold")) if p.get("observed_at") else None
            if observed and parse(observed) > self.clock():
                raise AssistantError("future_observation", "Observation time cannot be in the future.")
            plan["steps"].append({"uuid": uid, "before": None, "after": row, "create": row})
            plan["effect"] = {"meta_add": [uid, p.get("source", "general"), p.get("person"),
                p.get("reference"), observed, now, now, p.get("zone"), p.get("estimate_minutes")]}
            continuation=continuity_values(p)
            if continuation and not (self.store.state_value('synthetic_bootstrap',False) or self.store.state_value('connected',False)):
                raise AssistantError('proposal_required','Continuity capture requires an initialized connected workspace.')
            if continuation:plan['effect']['continuity_set']=[uid,continuation,now]
            plan["result"] = {"uuid": uid}
        elif action in ("task_update", "task_snooze", "task_done", "front_activate", "front_todo", "front_retire", "task_restore", "dependency_update", "task_reopen"):
            row = self.required(p["uuid"])
            changes = {}
            if action == "task_restore":
                if canonical(row) != p["expected_after"]:
                    raise AssistantError("undo_conflict", "The responsibility changed after this fact; nothing was overwritten.")
                changes = p["patch"]
                if not changes or set(changes) - {"description", "priority", "due", "wait", "start", "status", "end", "depends"}:
                    raise AssistantError("invalid_restore", "The inverse exceeds the fields of that decision.")
                if changes.get('start',row.get('start')) and changes.get('status',row['status'])=='pending':
                    by_id={item['uuid']:item for item in self.tasks.rows()}
                    if self.blockers(dict(row,depends=changes.get('depends',row.get('depends')) or []),by_id):
                        raise AssistantError('not_actionable','The inverse cannot activate a responsibility whose dependencies are now pending.')
                if 'depends' in changes:self.validate_dependencies(row['uuid'],changes['depends'] or [])
            elif action=='dependency_update':
                wanted=self.validate_dependencies(row['uuid'],p['dependsOn'])
                changes={'depends':wanted or None}
            elif action in ("front_activate", "front_todo", "front_retire"):
                if row["status"] != "pending":
                    raise AssistantError("terminal_task", "The responsibility is already closed.")
                if action == "front_activate":
                    by_id = {item["uuid"]: item for item in self.tasks.rows()}
                    if self.blockers(row,by_id):
                        raise AssistantError("not_actionable", "The responsibility depends on another pending task.")
                    changes = {"start": row.get("start") or wire(now), "wait": None}
                elif action == "front_todo":
                    changes = {"start": None, "wait": None}
                else:
                    changes = {"status": "deleted", "end": wire(now), "start": None, "wait": None}
            elif action=='task_reopen':
                if row['status'] not in ('completed','deleted'):raise AssistantError('not_closed','Only a completed or retired responsibility can be reopened.')
                changes={'status':'pending','end':None,'start':None,'wait':None}
            elif action == "task_done":
                if row['status']=='deleted':
                    raise AssistantError('terminal_task','Restore a retired responsibility before marking it complete.')
                changes = {} if row['status']=='completed' else {"status": "completed", "end": wire(now), "start": None, "wait": None}
            elif action == "task_snooze":
                if row["status"] != "pending":
                    raise AssistantError("terminal_task", "Only pending tasks can be snoozed.")
                until = instant(p["until"], p.get("zone"), p.get("fold"))
                if parse(until) <= self.clock():
                    raise AssistantError("past_snooze", "Snooze must end in the future.")
                changes = {"wait": wire(until), "start": None}
            else:
                if p.get("title") is not None:
                    if not p["title"].strip() or len(p["title"]) > 500:
                        raise AssistantError("invalid_title", "Invalid task title.")
                    changes["description"] = p["title"].strip()
                if p.get("priority"):
                    changes["priority"] = PRIORITIES[p["priority"]]
                if "due" in p:
                    changes["due"] = wire(instant(p["due"], p.get("zone"), p.get("fold"))) if p["due"] else None
                if p.get("progress"):
                    plan["effect"]["progress"] = [row["uuid"], now]
                    if row.get("wait") and parse(row["wait"]) <= self.clock():
                        changes["wait"] = None
                continuation=continuity_values(p)
                if continuation:
                    with self.store.connect() as con:
                        record=con.execute('SELECT data FROM continuity WHERE uuid=?',(row['uuid'],)).fetchone()
                    previous=json.loads(record[0]) if record else {}
                    if any(previous.get(k)!=v for k,v in continuation.items()):plan['effect']['continuity_set']=[row['uuid'],continuation,now]
                if not changes and not plan["effect"]:
                    raise AssistantError("empty_update", "No change was supplied.")
            patch(row, changes)
            plan["result"] = {"uuid": row["uuid"]}
        elif action=='related_update':
            uid=self.required(p['uuid'])['uuid'];refs=p['relatedTo']
            if not isinstance(refs,list) or len(refs)>100 or any(not isinstance(x,str) for x in refs):raise AssistantError('invalid_relations','Related responsibilities must be a bounded UUID list.')
            wanted=sorted({self.required(x)['uuid'] for x in refs})
            if uid in wanted:raise AssistantError('invalid_relations','A responsibility cannot be related to itself.')
            with self.store.connect() as con:
                current=sorted(r['b'] if r['a']==uid else r['a'] for r in con.execute('SELECT * FROM related WHERE a=? OR b=?',(uid,uid)))
            if current!=wanted:plan['effect']['related_set']=[uid,wanted]
            plan['result']={'uuid':uid}
        elif action=='provenance_update':
            uid=self.required(p['uuid'])['uuid'];fields={'source','person','reference','observed_at'}
            if set(p)-fields-{'uuid','zone'}:raise AssistantError('invalid_provenance','Unknown provenance fields.')
            value={k:p[k] for k in fields if k in p}
            if 'observed_at' in value and value['observed_at']:
                value['observed_at']=instant(value['observed_at'],p.get('zone'))
                if parse(value['observed_at'])>self.clock():raise AssistantError('future_observation','Observation time cannot be in the future.')
            previous=self.store.metadata().get(uid,{})
            if any(previous.get(k)!=v for k,v in value.items()):plan['effect']['meta_update']=[uid,value]
            plan['result']={'uuid':uid,'declared':True}
        elif action in ("context_update", "continuity_update"):
            if action == "context_update":
                current = self.store.state_value("day_context", {"availableMinutes": None, "availableUntil": None, "rest": False})
                if set(p) - {"availableMinutes", "availableUntil", "rest"}:
                    raise AssistantError("invalid_context", "Invalid context declaration.")
                value = dict(current, **p)
                minutes, until = value["availableMinutes"], value["availableUntil"]
                availability_supplied=bool({'availableMinutes','availableUntil'} & set(p))
                if minutes is not None and availability_supplied:
                    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not math.isfinite(minutes) or minutes <= 0 or not until:
                        raise AssistantError("invalid_context", "Provide positive minutes and the end of the availability window.")
                    value["availableUntil"] = instant(until)
                    duration = (parse(value["availableUntil"]) - self.clock()).total_seconds()/60
                    if duration <= 0 or minutes > duration:
                        raise AssistantError("invalid_context", "Minutes cannot exceed the future duration of the window.")
                elif minutes is None:
                    value["availableUntil"] = None
                if not isinstance(value["rest"], bool):
                    raise AssistantError("invalid_context", "Rest requires an explicit declaration.")
                availability_changed=any(value.get(k)!=current.get(k) for k in ('availableMinutes','availableUntil'))
                if availability_changed or 'declaredAt' not in value:value['declaredAt']=now
                if any(value.get(k)!=current.get(k) for k in ('availableMinutes','availableUntil','rest')):
                    plan["effect"]["context_set"] = value
            else:
                uid = self.required(p["uuid"])["uuid"]
                allowed = {"remainingMinutes", "awaiting", "context", "next"}
                if set(p) - allowed - {"uuid"}:
                    raise AssistantError("invalid_continuity", "Invalid declaration.")
                value = continuity_values(p)
                with self.store.connect() as con:
                    existing=con.execute('SELECT data FROM continuity WHERE uuid=?',(uid,)).fetchone()
                previous=json.loads(existing[0]) if existing else {}
                if any(previous.get(k)!=v for k,v in value.items()):
                    plan["effect"]["continuity_set"] = [uid, value, now]
            plan["result"] = {"declared": True}
        elif action in ("focus_push", "focus_resume"):
            rows = self.tasks.rows()
            by_id = {r["uuid"]: r for r in rows}
            frames = self.store.frames()
            current = frames[-1]["target"] if frames else None
            active = [r["uuid"] for r in rows if r["status"] == "pending" and r.get("start")]
            if any(uid != current for uid in active):
                raise AssistantError("external_focus", "Taskwarrior has an active task outside the focus stack.")
            if action == "focus_push":
                uid = self.validate_uuid(p["uuid"])
                target = by_id.get(uid)
                if not self.actionable(target, by_id):
                    raise AssistantError("not_actionable", "The target is completed, blocked or snoozed.")
                if current == uid:
                    plan["result"] = {"focus": uid, "unchanged": True}
                    return plan
                if current and by_id.get(current, {}).get("start"):
                    patch(by_id[current], {"start": None})
                patch(target, {"start": wire(now), "wait": None})
                plan["effect"] = {"frame_push": [key, uid, current]}
                plan["result"] = {"focus": uid, "paused": current}
            else:
                if not frames:
                    raise AssistantError("empty_focus", "There is no focus frame to resume.")
                frame = frames[-1]
                previous = frame["previous"]
                valid = previous is None or self.actionable(by_id.get(previous), by_id)
                if not valid and not p.get("skip_invalid"):
                    raise AssistantError("invalid_frame", "The previous task is unavailable; explicitly skip this frame.", {"previous": previous})
                if current and by_id.get(current, {}).get("start"):
                    patch(by_id[current], {"start": None})
                restored = previous if valid else None
                if restored:
                    patch(by_id[restored], {"start": wire(now)})
                plan["effect"] = {"frame_pop": frame["id"]}
                plan["result"] = {"focus": restored, "skipped": previous if not valid else None}
        elif action == "event_add":
            start = instant(p["start"], p.get("zone"), p.get("fold"))
            end = instant(p["end"], p.get("zone"), p.get("fold"))
            if parse(end) <= parse(start) or not p["title"].strip():
                raise AssistantError("invalid_event", "An event needs a title and an end after its start.")
            uid = str(uuid.uuid4())
            plan["effect"] = {"event_add": [uid, p["title"].strip(), start, end, p.get("zone"), p["start"]]}
            if self.store.state_value('connected',False):
                metadata={k:p[k] for k in ('source','person','reference','observed_at') if k in p}
                if metadata.get('observed_at'):metadata['observed_at']=instant(metadata['observed_at'],p.get('zone') or self.store.state_value('timezone'),p.get('fold'))
                plan['effect']['event_meta']=[uid,metadata|{'captured_at':now}]
            plan["result"] = {"uuid": uid}
        elif action=='event_update':
            uid=self.validate_uuid(p['uuid'])
            with self.store.connect() as con:old=con.execute('SELECT * FROM events WHERE uuid=? AND canceled=0',(uid,)).fetchone()
            if not old:raise AssistantError('event_not_found','The event does not exist or was canceled.')
            value=dict(old);changes={}
            for key,column in [('title','title'),('start','start_at'),('end','end_at')]:
                if key in p:
                    result=instant(p[key],p.get('zone'),p.get('fold')) if key!='title' else p[key].strip()
                    if key=='title' and (not result or len(result)>500):raise AssistantError('invalid_event','Invalid event title.')
                    if value[column]!=result:changes[column]=result;value[column]=result
            if parse(value['end_at'])<=parse(value['start_at']):raise AssistantError('invalid_event','The event must end after it starts.')
            if changes:plan['effect']['event_patch']=[uid,changes]
            plan['result']={'uuid':uid}
        elif action == "event_cancel":
            uid = self.validate_uuid(p["uuid"])
            with self.store.connect() as con:event=con.execute('SELECT canceled FROM events WHERE uuid=?',(uid,)).fetchone()
            if not event:
                raise AssistantError("event_not_found", "The event does not exist.")
            if not event['canceled']:plan["effect"] = {"event_cancel": uid}
            plan["result"] = {"uuid": uid}
        elif action == "config_set":
            if p["name"] not in DEFAULTS or not isinstance(p["value"], int) or not 1 <= p["value"] <= 10080:
                raise AssistantError("invalid_config", "Unknown setting or value outside 1–10080.")
            plan["effect"] = {"config": [p["name"], p["value"]]}
            plan["result"] = p
        else:
            raise AssistantError("unknown_action", "Unsupported mutation.")
        # Persist both read and write preconditions for recovery.
        native=self.tasks.rows();written={step['uuid'] for step in plan['steps']}
        plan['reads']={r['uuid']:canonical(r) for r in native if r['uuid'] not in written}
        plan['known_uuids']=sorted({r['uuid'] for r in native}|written)
        return plan

    def read_observations(self,plan):
        if 'reads' not in plan:return {'matching':True,'participants':[],'unexpected':[]}
        rows={r['uuid']:canonical(r) for r in self.tasks.rows()}
        participants=[{'uuid':uid,'expected':expected,'observed':rows.get(uid)} for uid,expected in plan['reads'].items()]
        # A not-yet-created write participant is optional until its own step.
        mandatory=set(plan.get('known_uuids',[]))-{s['uuid'] for s in plan['steps'] if s.get('before') is None}
        unexpected=sorted(set(rows)-set(plan.get('known_uuids',[])))
        return {'matching':not unexpected and mandatory<=set(rows) and all(p['expected']==p['observed'] for p in participants),'participants':participants,'unexpected':unexpected}

    def validate_reads(self,plan):
        observations=self.read_observations(plan)
        if not observations['matching']:raise AssistantError('external_conflict','A prerequisite or reviewed native participant changed after admission; no additional effect was authorized.',observations)

    def _apply(self, key, plan):
        self.store.assert_writable()
        try:
            if self.store.state_value("connected", False):
                with self.store.connect() as con:
                    admitted = con.execute("SELECT 1 FROM decisions WHERE operation_key=? AND proposal_id=?", (key, plan.get("proposal_id"))).fetchone()
                if not admitted:
                    raise AssistantError("authorization_missing", "The connected operation has no durable authorization.")
            with self.store.connect() as con:
                con.execute("UPDATE operations SET status='applying',error=NULL WHERE key=?", (key,))
            if self.store.revision() != plan["revision"]:
                raise AssistantError("external_conflict", "The auxiliary revision changed during an incomplete operation.")
            self.validate_reads(plan)
            # Validate every participant before executing any remaining step.
            # A conflict in a later participant must not cause an earlier one
            # to acquire another partial effect during recovery.
            for step in plan["steps"]:
                current = canonical(self.tasks.get(step["uuid"]))
                if current not in (step["before"], step["after"]):
                    raise AssistantError("external_conflict", "A participant changed; no additional step was attempted.", {"uuid": step["uuid"]})
            for index, step in enumerate(plan["steps"]):
                current = canonical(self.tasks.get(step["uuid"]))
                if current == step["after"]:
                    continue
                if current != step["before"]:
                    raise AssistantError("external_conflict", "Taskwarrior changed outside this operation; no overwrite attempted.", {"uuid": step["uuid"]})
                if "create" in step:
                    self.tasks.create(step["create"])
                else:
                    self.tasks.patch(step["uuid"], step["patch"])
                self.fault(f"after_task_{index}")
                if canonical(self.tasks.get(step["uuid"])) != step["after"]:
                    raise AssistantError("external_conflict", "The observed task differs from the intended effect.", {"uuid": step["uuid"]})
            self.fault("before_commit")
            for step in plan["steps"]:
                if canonical(self.tasks.get(step["uuid"])) != step["after"]:
                    raise AssistantError("external_conflict", "A task changed before the auxiliary commit.", {"uuid": step["uuid"]})
            self.validate_reads(plan)
            result = dict(plan["result"], revision=plan["revision"] + 1)
            effect = plan["effect"]
            with self.store.connect() as con:
                if "meta_add" in effect:
                    con.execute("INSERT INTO task_meta VALUES(?,?,?,?,?,?,?,?,?)", effect["meta_add"])
                if 'attachments_link' in effect:
                    uid,refs=effect['attachments_link']
                    for ref in refs:con.execute('INSERT OR IGNORE INTO item_attachments VALUES(?,?)',(uid,ref))
                if "progress" in effect:
                    uid, at = effect["progress"]
                    con.execute("UPDATE task_meta SET human_progress_at=? WHERE uuid=?", (at, uid))
                if "frame_push" in effect:
                    con.execute("INSERT INTO frames(operation_key,target,previous) VALUES(?,?,?)", effect["frame_push"])
                if "frame_pop" in effect:
                    con.execute("DELETE FROM frames WHERE id=?", (effect["frame_pop"],))
                if "event_add" in effect:
                    con.execute("INSERT INTO events(uuid,title,start_at,end_at,origin_zone,original_start) VALUES(?,?,?,?,?,?)", effect["event_add"])
                if "event_cancel" in effect:
                    con.execute("UPDATE events SET canceled=1 WHERE uuid=?", (effect["event_cancel"],))
                if 'event_meta' in effect:
                    uid,values=effect['event_meta']
                    for field,value in values.items():con.execute('UPDATE events SET '+field+'=? WHERE uuid=?',(value,uid))
                if 'event_patch' in effect:
                    uid,values=effect['event_patch']
                    for field,value in values.items():con.execute('UPDATE events SET '+field+'=? WHERE uuid=?',(value,uid))
                if 'meta_update' in effect:
                    uid,values=effect['meta_update']
                    con.execute('INSERT OR IGNORE INTO task_meta(uuid,captured_at,human_progress_at) VALUES(?,?,NULL)',(uid,self.now()))
                    for field,value in values.items():
                        if con.execute('UPDATE task_meta SET '+field+'=? WHERE uuid=?',(value,uid)).rowcount!=1:raise AssistantError('metadata_missing','Source metadata was not persisted.')
                if 'related_set' in effect:
                    uid,refs=effect['related_set'];con.execute('DELETE FROM related WHERE a=? OR b=?',(uid,uid))
                    for other in refs:con.execute('INSERT OR IGNORE INTO related VALUES(?,?)',tuple(sorted((uid,other))))
                if "config" in effect:
                    name, value = effect["config"]
                    con.execute("UPDATE config SET value=? WHERE key=?", (value, name))
                if "context_set" in effect:
                    con.execute("INSERT OR REPLACE INTO state VALUES('day_context',?)", (encoded(effect["context_set"]),))
                if "continuity_set" in effect:
                    uid, value, at = effect["continuity_set"]
                    old = con.execute("SELECT data FROM continuity WHERE uuid=?", (uid,)).fetchone()
                    merged = dict(json.loads(old[0]) if old else {}, **value, declaredAt=at)
                    con.execute("INSERT OR REPLACE INTO continuity VALUES(?,?)", (uid, encoded(merged)))
                if plan.get("proposal_id"):
                    con.execute("UPDATE proposals SET phase='applied',result=? WHERE id=?", (encoded(result), plan["proposal_id"]))
                    con.execute("INSERT OR IGNORE INTO facts(operation_key,proposal_id,action,plan,at) VALUES(?,?,?,?,?)", (key, plan["proposal_id"], plan["action"], encoded(plan), self.now()))
                con.execute("UPDATE state SET value=? WHERE key='revision'", (str(result["revision"]),))
                con.execute("UPDATE operations SET status='confirmed',result=?,error=NULL WHERE key=?", (encoded(result), key))
                self.fault("inside_commit")
            self.fault("after_commit")
            return result
        except AssistantError as error:
            status = "conflict" if error.code == "external_conflict" else "applying"
            with self.store.connect() as con:
                con.execute("UPDATE operations SET status=?,error=? WHERE key=?", (status, str(error), key))
            raise

    def _view(self,coherent=True):
        if coherent:self.clean_read()
        rows = self.tasks.rows()
        by_id = {r["uuid"]: r for r in rows}
        meta = self.store.metadata()
        frames = self.store.frames()
        focused = frames[-1]["target"] if frames else None
        view = []
        for row in rows:
            uid = row["uuid"]
            status = row["status"]
            if status == "pending" and row.get("wait") and parse(row["wait"]) > self.clock():
                status = "waiting"
            elif status == "pending" and not self.actionable(row, by_id):
                status = "blocked"
            view.append({"uuid": uid, "title": row["description"], "status": status,
                         "priority": PUBLIC_PRIORITY.get(row.get("priority"), "medium"),
                         "due_at": stamp(parse(row["due"])) if row.get("due") else None,
                         "snoozed_until": stamp(parse(row["wait"])) if row.get("wait") else None,
                         "focused": bool(row.get("start")) and row["status"]=="pending" if self.store.state_value("connected",False) else uid==focused and bool(row.get("start")) and status=="pending",
                         "dependsOn":row.get('depends',[]),"blockedBy":self.blockers(row,by_id) if row['status']=='pending' else [],
                         "metadata": meta.get(uid, {})})
        return view

    def list(self, include_completed=False):
        with self.store.lock():
            rows = self._view()
            return {"revision": self.store.revision(), "tasks": [r for r in rows if include_completed or r["status"] not in ("completed", "deleted")]}

    def _candidates(self, view):
        now = self.clock()
        cfg = self.store.config()
        result = []
        for task in view:
            if task["status"] in ("completed", "deleted", "waiting"):
                continue
            uid = task["uuid"]
            def add(kind, episode, title):
                identity = fingerprint([uid, kind, episode])
                result.append({"id": identity, "entity": uid, "kind": kind,
                               "episode": episode, "title": title, "task": task})
            due = task["due_at"]
            if due and parse(due) <= now + timedelta(minutes=cfg["due_soon_minutes"]):
                add("due", due, task["title"])
            wait = task["snoozed_until"]
            if wait and parse(wait) <= now:
                add("snooze_ready", wait, task["title"])
            info = task["metadata"]
            progress = info.get("human_progress_at")
            if progress and parse(progress) <= now - timedelta(days=cfg["stale_days"]):
                add("stale", progress, task["title"])
            observed = info.get("observed_at")
            if info.get("source") == "chat" and observed and parse(observed) <= now - timedelta(minutes=cfg["chat_wait_minutes"]):
                add("chat_waiting", observed, task["title"])
        for event in self.store.events():
            if parse(event["end_at"]) > now and parse(event["start_at"]) <= now + timedelta(minutes=cfg["event_soon_minutes"]):
                result.append({"id": fingerprint([event["uuid"], "event", event["start_at"]]),
                               "entity": event["uuid"], "kind": "event", "episode": event["start_at"], "title": event["title"]})
        return result

    def pulse(self):
        # This is a pure query: evaluating reminders never consumes suppression.
        with self.store.lock():
            return {"revision": self.store.revision(), "notices": self._candidates(self._view())}

    def brief(self):
        with self.store.lock():
            view = self._view()
            available = [t for t in view if t["status"] == "pending"]
            priority_rank = {"high": 0, "medium": 1, "low": 2}
            available.sort(key=lambda t: (not t["focused"], priority_rank[t["priority"]], t["due_at"] or "9999", t["uuid"]))
            next_task = available[0] if available else None
            events = self.store.events()
            clashes = []
            if next_task and next_task["metadata"].get("estimate_minutes"):
                finish = self.clock() + timedelta(minutes=next_task["metadata"]["estimate_minutes"])
                clashes = [e for e in events if parse(e["end_at"]) > self.clock() and parse(e["start_at"]) < finish]
            return {"revision": self.store.revision(), "next": next_task,
                    "order": "focus, explicit priority, due date, UUID", "conflicts": clashes,
                    "notices": self._candidates(view)}

    def notify(self, retry_uncertain=False):
        from .profiles import delivery_allowed
        from zoneinfo import ZoneInfo
        policy=self.store.state_value('notification_policy')
        def permitted():
            current=self.store.state_value('notification_policy')
            return delivery_allowed(current,self.clock(),self.store.state_value('timezone','UTC')) if current else (True,'legacy')
        allowed,reason=permitted()
        if not allowed:return {'attempts':[],'deferred':reason}
        results = []
        with self.store.lock():
            self.store.assert_writable()
            self.clean_read()
            now = self.now()
            # One reason per entity, ordered independently of backend export order.
            rank = {"event": 0, "due": 1, "snooze_ready": 2, "chat_waiting": 3, "stale": 4}
            candidates = sorted(self._candidates(self._view()),
                                key=lambda n: (rank[n["kind"]], n["episode"], n["entity"]))
            selected = {}
            for item in candidates:
                selected.setdefault(item["entity"], item)
            candidates = list(selected.values())
            hold=self.store.state_value('restore_suppression',{})
            if hold.get('until') and parse(hold['until'])>self.clock():candidates=[x for x in candidates if x['id'] not in hold.get('ids',[])]
            if policy:
                with self.store.connect() as con:
                    prior={r['entity']:r['last_at'] for r in con.execute("SELECT entity,max(accepted_at) last_at FROM notices WHERE accepted_at IS NOT NULL GROUP BY entity")}
                # Unnotified work precedes unchanged repeats; the rolling budget
                # cannot permanently starve later entities of the same urgency.
                candidates.sort(key=lambda n:(prior.get(n['entity']) is not None,prior.get(n['entity']) or '',rank[n['kind']],n['episode'],n['entity']))
            with self.store.connect() as con:
                for item in candidates:
                    con.execute("INSERT OR IGNORE INTO notices(id,episode,entity,kind,status) VALUES(?,?,?,?, 'pending')",
                                (item["id"], item["episode"], item["entity"], item["kind"]))
                valid = {item["id"] for item in self._candidates(self._view())}
                for row in con.execute("SELECT id FROM notices WHERE status NOT IN ('obsolete','sending')").fetchall():
                    if row["id"] not in valid:
                        con.execute("UPDATE notices SET status='obsolete' WHERE id=?", (row["id"],))
                expired = stamp(self.clock() - timedelta(minutes=2))
                con.execute("UPDATE attempts SET status='uncertain',error='Process outcome unknown' WHERE status='sending' AND at<=?", (expired,))
                con.execute("UPDATE notices SET status='uncertain',active_attempt_id=NULL,error='Process outcome unknown' WHERE status='sending' AND claimed_at<=?", (expired,))
        # Claims and result commits are short transactions; notifier runs outside
        # both the SQLite transaction and the coordinator lock.
        for item in candidates:
            with self.store.lock():
                self.store.assert_writable()
                self.clean_read()
                if not permitted()[0]:break
                current = {n["id"]: n for n in self._candidates(self._view())}
                if item["id"] not in current:
                    continue
                item = current[item["id"]]
                with self.store.connect() as con:
                    row = con.execute("SELECT * FROM notices WHERE id=?", (item["id"],)).fetchone()
                    # A shared rolling budget also covers concurrent/restarted
                    # clients and backward clock jumps. Failures consume budget.
                    since = stamp(self.clock() - timedelta(minutes=5))
                    if con.execute("SELECT count(*) FROM attempts WHERE at>?", (since,)).fetchone()[0] >= self.store.config()["notification_budget"]:
                        continue
                    current_policy=self.store.state_value('notification_policy')
                    if current_policy:
                        local=self.clock().astimezone(ZoneInfo(current_policy.get('timezone') or self.store.state_value('timezone','UTC')))
                        start=stamp(local.replace(hour=0,minute=0,second=0,microsecond=0))
                        if con.execute("SELECT count(*) FROM attempts WHERE at>=?",(start,)).fetchone()[0]>=current_policy['dailyBudget']:continue
                    siblings = con.execute("SELECT * FROM notices WHERE entity=?", (item["entity"],)).fetchall()
                    repeat = timedelta(minutes=current_policy["unchangedRepeatMinutes"] if current_policy else self.store.config()["repeat_minutes"])
                    if any(r["status"] == "sending" or
                           (r["status"] == "uncertain" and not retry_uncertain) or
                           (r["accepted_at"] and parse(r["accepted_at"]) + repeat > self.clock())
                           for r in siblings):
                        continue
                    if row["status"] in ("sending", "failed") or (row["status"] == "uncertain" and not retry_uncertain):
                        continue
                    if row["retry_at"] and parse(row["retry_at"]) > self.clock():
                        continue
                    repeat = timedelta(minutes=current_policy["unchangedRepeatMinutes"] if current_policy else self.store.config()["repeat_minutes"])
                    if row["accepted_at"] and parse(row["accepted_at"]) + repeat > self.clock():
                        continue
                    attempt_number = row["attempts"] + 1 if row["status"] != "accepted" else 1
                    if attempt_number > 3:
                        con.execute("UPDATE notices SET status='failed' WHERE id=?", (item["id"],))
                        continue
                    attempt = con.execute("INSERT INTO attempts(notice_id,at,status) VALUES(?,?,'sending')", (item["id"], self.now())).lastrowid
                    con.execute("UPDATE notices SET status='sending',attempts=?,claimed_at=?,active_attempt_id=? WHERE id=?", (attempt_number, self.now(), attempt, item["id"]))
            with platforms.exclusive(self.store.path/'delivery.lock',shared=True):
                self.fault("after_claim")
                self.fault("before_notify")
                with self.store.lock():
                    if self.store.state_value('maintenance') or not permitted()[0]:
                        with self.store.connect() as con:
                            changed=con.execute("UPDATE notices SET status='pending',attempts=max(0,attempts-1),active_attempt_id=NULL,claimed_at=NULL WHERE id=? AND status='sending' AND active_attempt_id=?",(item['id'],attempt)).rowcount
                            if changed:con.execute('DELETE FROM attempts WHERE id=?',(attempt,))
                        results.append({'id':item['id'],'status':'deferred','read_by_user':'unknown'})
                        continue
                    self.clean_read()
                    current = {n["id"]: n for n in self._candidates(self._view())}
                    with self.store.connect() as con:
                        owned = con.execute("SELECT 1 FROM notices WHERE id=? AND status='sending' AND active_attempt_id=?", (item["id"], attempt)).fetchone()
                    if not owned:
                        results.append({"id": item["id"], "status": "ownership_lost", "read_by_user": "unknown"})
                        continue
                    if item["id"] not in current:
                        with self.store.connect() as con:
                            con.execute("UPDATE attempts SET status='obsolete',error='Condition changed after claim' WHERE id=?", (attempt,))
                            con.execute("UPDATE notices SET status='obsolete',active_attempt_id=NULL,error='Condition changed after claim' WHERE id=? AND status='sending' AND active_attempt_id=?", (item["id"], attempt))
                        results.append({"id": item["id"], "status": "obsolete", "read_by_user": "unknown"})
                        continue
                    item = current[item["id"]]
                # A task may still change after this last check. This is a bounded
                # race with external delivery, not an atomic task+popup operation.
                outcome = self.notifier("Day Attendant", item["title"])
                self.fault("after_notify")
                status = "uncertain" if outcome.get("uncertain") else "accepted" if outcome.get("accepted") else "pending" if attempt_number < 3 else "failed"
                retry_at = stamp(self.clock() + timedelta(minutes=2 ** attempt_number)) if status == "pending" else None
                with self.store.lock(), self.store.connect() as con:
                    changed = con.execute("UPDATE notices SET status=?,active_attempt_id=NULL,error=?,retry_at=?,accepted_at=CASE WHEN ?='accepted' THEN ? ELSE accepted_at END WHERE id=? AND status='sending' AND active_attempt_id=?",
                                (status, outcome.get("error"), retry_at, status, self.now(), item["id"], attempt)).rowcount
                    if changed:
                        con.execute("UPDATE attempts SET status=?,error=? WHERE id=?", (status, outcome.get("error"), attempt))
                    else:
                        con.execute("UPDATE attempts SET late_outcome=? WHERE id=?", (encoded(outcome), attempt))
                        status = "ownership_lost"
                results.append({"id": item["id"], "status": status, "read_by_user": "unknown"})
        return {"attempts": results}

    def doctor(self):
        with self.store.lock():
            caps = platforms.capabilities()
            with self.store.connect() as con:
                integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
                notices = [dict(r) for r in con.execute("SELECT id,status,attempts,error,claimed_at FROM notices WHERE status IN ('sending','uncertain','failed')")]
            for notice in notices:
                expired = (notice["status"] == "sending" and notice["claimed_at"]
                           and parse(notice["claimed_at"]) + timedelta(minutes=2) <= self.clock())
                notice["claim_expired"] = bool(expired)
                notice["delivery_state"] = "expired_unknown" if expired else notice["status"]
                notice["recommended_action"] = ("Run notify to reconcile the expired claim; review before explicit retry."
                    if expired else "Review before retrying an uncertain delivery." if notice["status"] == "uncertain"
                    else "Wait for the active delivery." if notice["status"] == "sending"
                    else "Review the notifier failure; automatic retry limit was reached.")
            return {"version": self.tasks.version(), "state": str(self.store.path), "integrity": integrity,
                    "revision": self.store.revision(), "operations": self.store.unresolved(),
                    "notices_requiring_attention": notices, "capabilities": caps,
                    "config": self.store.config(), "scheduler_installed_by_this_app": bool((self.store.state_value("scheduler") or {}).get('installed')), "scheduler":self.store.state_value("scheduler"), "last_tick":self.store.state_value("last_tick"), "last_backup":self.store.state_value("last_backup"), "notification_policy":self.store.state_value("notification_policy"), "maintenance":self.store.state_value("maintenance")}
