"""Human-readable Spanish/English output and a stable JSON contract for agents."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import uuid

from .errors import AssistantError
from .platforms import capabilities
from .service import Assistant, PRIORITIES

SPANISH_ERRORS = {
    "timezone_required": "Incluye un desplazamiento UTC o una zona IANA.",
    "nonexistent_time": "Esa hora local no existe por el cambio de horario.",
    "ambiguous_time": "Esa hora se repite; aclara si corresponde a fold 0 o 1.",
    "invalid_time": "Usa una fecha ISO 8601 y una zona IANA válida.",
    "unsafe_state_path": "Los datos privados deben quedar fuera del proyecto.",
    "task_missing": "La conversión de datos antiguos requiere Taskwarrior 3.5.0; usa --taskwarrior-bin.",
    "unsupported_task_version": "La conversión de datos antiguos requiere Taskwarrior 3.5.0.",
    "key_required": "La operación requiere una clave de idempotencia.",
    "idempotency_conflict": "Esa clave ya se usó para otro contenido.",
    "recovery_required": "Ejecuta recover; hay una operación pendiente de reconciliar.",
    "external_conflict": "Se detectó un cambio externo; no se sobrescribieron los datos.",
    "stale_revision": "El estado cambió; consúltalo antes de reintentar.",
    "revision_required": "Indica la última revisión observada con --expect.",
    "invalid_frame": "La tarea anterior no está disponible; revisa el motivo antes de omitir el marco.",
    "not_actionable": "La tarea está cerrada, bloqueada o pospuesta.",
    "empty_focus": "No hay un marco de foco para retomar.",
    "task_uncertain": "Taskwarrior agotó el tiempo; ejecuta recover antes de reintentar.",
    "past_snooze": "La posposición debe terminar en el futuro.",
    "invalid_arguments": "Argumentos inválidos; consulta --help.",
    "future_observation": "La hora de observación no puede estar en el futuro.",
    "proposal_required": "Este espacio conectado usa propuestas. Opera desde la UI o con board request/confirm.",
    "state_required": "Indica --state con el espacio ficticio conectado.",
    "server_unavailable": "Inicia el servicio web de ese espacio y vuelve a intentar.",
}


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise AssistantError("invalid_arguments", message)


def parser():
    common = Parser(add_help=False, argument_default=argparse.SUPPRESS)
    common.add_argument("--state")
    # Compatibility for previously installed scheduler commands; never executed.
    common.add_argument("--task-bin", help=argparse.SUPPRESS)
    common.add_argument("--json", action="store_true")
    common.add_argument("--lang", choices=["es", "en"])
    root = Parser(prog="day-attendant", parents=[common])
    groups = root.add_subparsers(dest="group", required=True, parser_class=Parser)

    def command(parent, name, mutation=False, focus=False):
        p = parent.add_parser(name, parents=[common])
        if mutation:
            p.add_argument("--key", required=True)
            p.add_argument("--expect", type=int, required=focus)
        return p

    def temporal(p):
        p.add_argument("--zone")
        p.add_argument("--fold", type=int, choices=[0, 1])

    task = command(groups, "task").add_subparsers(dest="command", required=True, parser_class=Parser)
    p = command(task, "add", True)
    p.add_argument("title")
    p.add_argument("--priority", choices=PRIORITIES, default="medium")
    p.add_argument("--due")
    p.add_argument("--source", default="general", choices=["general", "ticket", "pr", "chat", "email", "call", "research"])
    p.add_argument("--person")
    p.add_argument("--reference")
    p.add_argument("--observed-at")
    p.add_argument("--estimate-minutes", type=int)
    temporal(p)
    p = command(task, "list")
    p.add_argument("--all", action="store_true")
    p = command(task, "update", True)
    p.add_argument("uuid")
    p.add_argument("--title")
    p.add_argument("--priority", choices=PRIORITIES)
    due = p.add_mutually_exclusive_group()
    due.add_argument("--due", default=argparse.SUPPRESS)
    due.add_argument("--clear-due", action="store_true")
    p.add_argument("--progress", action="store_true")
    temporal(p)
    p = command(task, "done", True)
    p.add_argument("uuid")
    p = command(task, "snooze", True)
    p.add_argument("uuid")
    p.add_argument("until")
    temporal(p)
    focus = command(groups, "focus").add_subparsers(dest="command", required=True, parser_class=Parser)
    p = command(focus, "push", True, True)
    p.add_argument("uuid")
    p = command(focus, "resume", True, True)
    p.add_argument("--skip-invalid", action="store_true")
    event = command(groups, "event").add_subparsers(dest="command", required=True, parser_class=Parser)
    p = command(event, "add", True)
    p.add_argument("title")
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    temporal(p)
    command(event, "list")
    p = command(event, "cancel", True)
    p.add_argument("uuid")
    config = command(groups, "config").add_subparsers(dest="command", required=True, parser_class=Parser)
    command(config, "list")
    p = command(config, "set", True)
    p.add_argument("name")
    p.add_argument("value", type=int)
    for name in ("brief", "pulse", "doctor", "recover"):
        command(groups, name)
    p = command(groups, "notify")
    p.add_argument("--retry-uncertain", action="store_true")
    p = command(groups, "schedule")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--every-minutes", type=int, default=10)
    p = command(groups, "web")
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--port", type=int, default=0)
    p.add_argument("--zone")
    p.add_argument('--supervised',action='store_true',help=argparse.SUPPRESS)
    p=command(groups,'init')
    p.add_argument('--synthetic',action='store_true')
    p.add_argument('--zone')
    p.add_argument('--name')
    p.add_argument('--harness',choices=['codex','claude'],default='codex')
    p.add_argument('--model')
    p.add_argument('--effort',help='Reasoning level offered by the harness, for example low, medium or high')
    profiles=command(groups,'profile').add_subparsers(dest='command',required=True,parser_class=Parser)
    command(profiles,'show')
    p=command(profiles,'configure',True)
    p.add_argument('--values',required=True,help='JSON settings object')
    command(profiles,'upgrade')
    p=command(profiles,'migrate')
    p.add_argument('--taskwarrior-bin',help='Legacy Taskwarrior 3.5.0 executable; used only for this explicit offline migration')
    backups=command(groups,'backup').add_subparsers(dest='command',required=True,parser_class=Parser)
    p=command(backups,'create');p.add_argument('--destination');p.add_argument('--key')
    command(backups,'list')
    p=command(backups,'verify');p.add_argument('archive')
    p=command(groups,'restore');p.add_argument('archive');p.add_argument('--to',required=True);p.add_argument('--taskwarrior-bin',help='Optional one-time conversion of a legacy backup')
    service=command(groups,'service').add_subparsers(dest='command',required=True,parser_class=Parser)
    for name in ('start','stop','restart','status','open'):command(service,name)
    sched=command(groups,'scheduler').add_subparsers(dest='command',required=True,parser_class=Parser)
    for name in ('install','uninstall','status'):command(sched,name)
    command(groups,'tick')
    p=command(groups,'reconcile');p.add_argument('operation_key');p.add_argument('--resolution',choices=['complete','keep_observed']);p.add_argument('--precondition')
    board = command(groups, "board").add_subparsers(dest="command", required=True, parser_class=Parser)
    command(board, "list")
    p = command(board, "request", True)
    p.add_argument("action")
    p.add_argument("--payload", required=True)
    for name in ("cancel", "retry", "confirm"):
        p = command(board, name)
        p.add_argument("proposal_id")
        if name == "confirm":
            p.add_argument("--generation", type=int, required=True)
            p.add_argument("--without-review", action="store_true")
    p = command(board, "undo", True)
    p.add_argument("fact_id", type=int)
    p = command(board, "undo-message", True)
    p.add_argument("message_key")
    for name in ("accept", "dismiss"):
        p = command(board, name)
        p.add_argument("suggestion_id")
    command(board, "clear", True)
    command(board, "suggest", True)
    p = command(board, "chat", True)
    p.add_argument("text")
    p.add_argument("--image",action="append",default=[])
    p.add_argument("--capture-only",action="store_true")
    return root


def schedule(app, every):
    if every not in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60):
        raise AssistantError("invalid_interval", "Use an interval that divides 60 minutes.")
    caps = capabilities()
    argv = [sys.executable, str(Path(__file__).resolve().parent.parent / "day-attendant.py"),
            "--state", str(app.store.path), "notify"]
    if caps["platform"] == "darwin":
        backend = "launchd"
    else:
        backend = "systemd-user" if caps["user_systemd"] else "cron" if caps["crontab"] else "manual"
    cron = ("0 * * * *" if every == 60 else f"*/{every} * * * *") + " " + shlex.join(argv)
    return {"dry_run": True, "installed": False, "backend": backend, "argv": argv,
            "interval_minutes": every, "cron_example": cron if backend == "cron" else None,
            "note": "Choose working hours before installing any job. This command changes no scheduler."}


def execute(app, args):
    values = vars(args)
    group = args.group
    command = values.get("command")
    ignored = {"state", "task_bin", "json", "lang", "group", "command", "key", "expect"}
    payload = {k: v for k, v in values.items() if k not in ignored}
    if group == "task" and command == "list":
        return app.list(values.get("all", False))
    if group == "task" and command == "update" and payload.pop("clear_due", False):
        payload["due"] = None
    if group in ("task", "focus", "event", "config") and command not in ("list",):
        return app.mutate(f"{group}_{command}", payload, args.key, values.get("expect"))
    if group == "event":
        with app.store.lock():
            app.clean_read()
            return {"events": app.store.events(), "revision": app.store.revision()}
    if group == "config":
        return app.store.config()
    if group == "brief":
        return app.brief()
    if group == "pulse":
        return app.pulse()
    if group == "notify":
        return app.notify(values.get("retry_uncertain", False))
    if group == "recover":
        return app.recover()
    if group == "doctor":
        return app.doctor()
    if group == "schedule":
        return schedule(app, args.every_minutes)
    raise AssistantError("invalid_arguments", "Unsupported command.")


def human(data, language, group):
    es = language == "es"
    if group == "brief":
        task = data["next"]
        if not task:
            return "Sin tareas accionables." if es else "No actionable tasks."
        lines = [("Sigue: " if es else "Next: ") + f"{task['title']} ({task['uuid']})"]
        priority = {"high": "alta", "medium": "media", "low": "baja"}.get(task["priority"]) if es else task["priority"]
        lines.append(("Prioridad: " if es else "Priority: ") + priority)
        lines += [("Choque de agenda: " if es else "Calendar conflict: ") + e["title"] for e in data["conflicts"]]
        return "\n".join(lines[:8])
    if "tasks" in data:
        priorities = {"high": "alta", "medium": "media", "low": "baja"}
        states = {"pending": "pendiente", "waiting": "pospuesta", "blocked": "bloqueada", "completed": "terminada", "deleted": "eliminada"}
        return "\n".join(f"{t['uuid']} · {t['title']} · {priorities.get(t['priority'], t['priority']) if es else t['priority']} · {states.get(t['status'], t['status']) if es else t['status']}" for t in data["tasks"][:8]) or ("Sin tareas pendientes." if es else "No pending tasks.")
    if group == "pulse":
        kinds = {"due": "vence pronto o está vencida", "stale": "sin avance registrado", "chat_waiting": "mensaje capturado pendiente", "event": "evento próximo o en curso", "snooze_ready": "terminó la posposición"}
        return "\n".join(f"{kinds.get(n['kind'], n['kind']) if es else n['kind']} · {n['title']}" for n in data["notices"][:8])
    if group == "notify":
        states = {"accepted": "aceptado por el sistema local", "uncertain": "resultado incierto", "pending": "pendiente de reintento", "failed": "falló"}
        return "\n".join(f"{r['id'][:10]} · {states.get(r['status'], r['status']) if es else r['status']}" for r in data["attempts"][:8])
    if group == "doctor":
        label = "Estado" if es else "State"
        return f"{data['backend']} {data['version']} · SQLite {data['integrity']}\n{label}: {data['state']}\n" + (
            f"Operaciones pendientes: {len(data['operations'])}" if es else f"Unresolved operations: {len(data['operations'])}")
    if "uuid" in data:
        return ("Guardado: " if es else "Saved: ") + data["uuid"]
    if "focus" in data:
        return ("Foco: " if es else "Focus: ") + str(data["focus"] or ("ninguno" if es else "none"))
    return json.dumps(data, ensure_ascii=False, indent=2)


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    wants_json = "--json" in raw
    language = "es" if "--lang" in raw and raw[raw.index("--lang") + 1:raw.index("--lang") + 2] == ["es"] else "en"
    operation_id = str(uuid.uuid4())
    try:
        args = parser().parse_args(raw)
        wants_json = getattr(args, "json", False)
        language = getattr(args, "lang", "en")
        operation_id = getattr(args, "key", operation_id)
        if args.group in ("web", "board",'init','profile','backup','restore','service','scheduler','tick','reconcile'):
            from .web import client, serve,initialize_profile,preflight
            from .profiles import default_state,configure
            state=getattr(args,'state',None) or default_state()
            if args.group == "web":
                serve(state,args.port,args.zone,kind='synthetic' if args.synthetic else None,supervised=args.supervised)
                return 0
            if args.group in ('backup','restore','service','scheduler','tick','reconcile'):
                from . import archives,lifecycle,recovery
                if args.group=='restore':data=archives.restore(args.archive,args.to,getattr(args,'taskwarrior_bin',None))
                elif args.group=='backup' and args.command=='verify':data=archives.verify(args.archive)
                else:
                    if args.group=='service' and args.command in ('stop','status'):
                        # Service ownership is readable even before an old task store is migrated.
                        from types import SimpleNamespace
                        from .store import Store
                        app=SimpleNamespace(store=Store(preflight(state)))
                    else:app=Assistant(preflight(state))
                    if not app.store.state_value('profile'):raise AssistantError('profile_required','Initialize a personal or synthetic profile first.')
                    if args.group=='backup':data=archives.list_backups(app) if args.command=='list' else archives.snapshot(app,args.destination,key=args.key)
                    elif args.group=='scheduler':data=lifecycle.scheduler(app,args.command)
                    elif args.group=='tick':data=lifecycle.tick(app)
                    elif args.group=='reconcile':
                        data=recovery.resolve(app,args.operation_key,args.resolution,args.precondition) if args.resolution else recovery.inspect(app,args.operation_key)
                    elif args.command=='start':data=lifecycle.start(app)
                    elif args.command=='stop':data=lifecycle.stop(app)
                    elif args.command=='restart':lifecycle.stop(app);data=lifecycle.start(app)
                    elif args.command=='open':
                        import webbrowser
                        lifecycle.start(app);url=lifecycle.session_url(app);opened=webbrowser.open(url);data={'opened':opened,'url':url}
                    else:data=lifecycle.status(app)
            elif args.group=='init':
                app=initialize_profile(state,'synthetic' if args.synthetic else 'personal',args.zone,args.name)
                changes={'harness':args.harness,'model':args.model,'effort':args.effort}
                configure(app,changes,'initial-harness:'+app.store.state_value('profile')['id'])
                data={'profile':app.store.state_value('profile'),'state':str(app.store.path),'tasks':len(app.tasks.rows())}
            elif args.group=='profile':
                if args.command=='migrate':
                    from .migration import migrate_profile
                    data=migrate_profile(preflight(state),args.taskwarrior_bin)
                elif args.command=='upgrade':
                    from .archives import upgrade_profile
                    data=upgrade_profile(state)
                else:
                    app=Assistant(preflight(state))
                    if args.command=='show':data={'profile':app.store.state_value('profile'),'notification_policy':app.store.state_value('notification_policy')}
                    else:
                        values=json.loads(args.values)
                        if (Path(state)/'ui-server.json').exists():data=client(state,'/api/settings',{'values':values,'key':args.key,'expected_revision':getattr(args,'expect',None)})
                        else:data=configure(app,values,args.key,getattr(args,'expect',None))
            elif args.command == "list":
                data = client(state)
            elif args.command == "request":
                try:
                    payload = json.loads(args.payload)
                except ValueError as error:
                    raise AssistantError("invalid_arguments", "--payload requires a JSON object.") from error
                data = client(state, "/api/proposals", {"action": args.action, "payload": payload,
                              "key": args.key, "expected_revision": getattr(args,"expect",None)})
            elif args.command == "chat":
                refs=[]
                for index,image in enumerate(args.image):
                    import base64,mimetypes
                    path=Path(image).expanduser()
                    uploaded=client(state,'/api/attachments',{'name':path.name,'mime':mimetypes.guess_type(path.name)[0] or '', 'base64':base64.b64encode(path.read_bytes()).decode(),'key':args.key+':image:'+str(index)})
                    refs.append(uploaded['id'])
                data = client(state, "/api/chat", {"text": args.text, "key": args.key,'attachments':refs,'capture_only':args.capture_only})
            elif args.command == "clear":
                data = client(state, "/api/chat/clear", {"key": args.key})
            elif args.command == "suggest":
                data = client(state, "/api/suggestion", {"key": args.key})
            elif args.command == "undo":
                data = client(state, "/api/undo", {"fact_id": args.fact_id, "key": args.key, "expected_revision": getattr(args,"expect",None)})
            elif args.command == "undo-message":
                data = client(state, "/api/undo", {"message_key": args.message_key, "key": args.key, "expected_revision": getattr(args,"expect",None)})
            elif args.command in ("accept", "dismiss"):
                data = client(state, "/api/suggestions/" + args.suggestion_id + "/" + args.command, {})
            else:
                body = {"generation": args.generation, "without_review": args.without_review} if args.command == "confirm" else {}
                data = client(state, "/api/proposals/" + args.proposal_id + "/" + args.command, body)
            if wants_json:
                print(json.dumps({"schema_version":1,"ok":True,"operation_id":operation_id,"data":data,"error":None},ensure_ascii=False))
            else:
                print(json.dumps(data,ensure_ascii=False,indent=2))
            return 0
        from .profiles import default_state
        default = default_state()
        explicit=getattr(args,'state',None) or os.environ.get('DAY_ATTENDANT_STATE')
        legacy=Path(os.environ.get('XDG_STATE_HOME',Path.home()/'.local/state'))/'asistente-dia'
        if not explicit and (os.environ.get('ASISTENTE_STATE') or legacy.exists()):
            raise AssistantError('state_selection_required','Legacy state exists. Select the intended path explicitly with --state; no data was moved or recreated.')
        app = Assistant(explicit or default)
        data = execute(app, args)
        if wants_json:
            print(json.dumps({"schema_version": 1, "ok": True, "operation_id": operation_id, "data": data, "error": None}, ensure_ascii=False))
        else:
            text = human(data, language, args.group)
            if text:
                print(text)
        return 0
    except (OSError,ValueError) as error:
        message="A file or JSON argument could not be read: "+str(error)[:300]
        if wants_json:print(json.dumps({"schema_version":1,"ok":False,"operation_id":operation_id,"data":None,"error":{"code":"invalid_arguments","message":message,"details":{}}}))
        else:print(message,file=sys.stderr)
        return 2
    except AssistantError as error:
        if wants_json:
            print(json.dumps({"schema_version": 1, "ok": False, "operation_id": operation_id,
                              "data": None, "error": {"code": error.code, "message": str(error), "details": error.details}}, ensure_ascii=False))
        else:
            message = SPANISH_ERRORS.get(error.code, f"No se pudo completar la operación ({error.code}).") if language == "es" else str(error)
            print(f"Error [{error.code}]: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
