"""Instrumented notification server, exclusively on the harness's private bus."""
import json
from pathlib import Path
import sys
from gi.repository import Gio, GLib

folder = Path(sys.argv[1])
xml = '''<node><interface name="org.freedesktop.Notifications">
<method name="GetCapabilities"><arg type="as" direction="out"/></method>
<method name="GetServerInformation"><arg type="s" direction="out"/><arg type="s" direction="out"/><arg type="s" direction="out"/><arg type="s" direction="out"/></method>
<method name="Notify"><arg type="s" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="as" direction="in"/><arg type="a{sv}" direction="in"/><arg type="i" direction="in"/><arg type="u" direction="out"/></method>
<method name="CloseNotification"><arg type="u" direction="in"/></method>
</interface></node>'''
counter = 0
held = []


def log(data):
    with (folder / 'dbus.jsonl').open('a') as stream:
        stream.write(json.dumps(data) + '\n')


def call(connection, sender, path, interface, method, params, invocation):
    global counter
    if method == 'GetCapabilities':
        invocation.return_value(GLib.Variant('(as)', (['body'],)))
    elif method == 'GetServerInformation':
        invocation.return_value(GLib.Variant('(ssss)', ('E2E', 'fixture', '1', '1.2')))
    elif method == 'CloseNotification':
        invocation.return_value(None)
    else:
        counter += 1
        identity = counter
        values = params.unpack()
        log({'stage': 'received', 'id': identity, 'app': values[0], 'title': values[3], 'body': values[4]})
        control = json.loads((folder / 'control.json').read_text())
        def reply():
            if control.get('mode') == 'fail':
                log({'stage': 'rejected', 'id': identity})
                invocation.return_dbus_error('org.freedesktop.DBus.Error.Failed', 'Injected rejection')
            else:
                log({'stage': 'accepted', 'id': identity})
                invocation.return_value(GLib.Variant('(u)', (identity,)))
            return False
        if control.get('mode') == 'hang':
            held.append(invocation)
        elif control.get('delay_ms'):
            GLib.timeout_add(control['delay_ms'], reply)
        else:
            reply()


def acquired(connection, name):
    connection.register_object('/org/freedesktop/Notifications', Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0], call, None, None)


def ready(connection, name):
    (folder / 'ready').write_text(name)


owner = Gio.bus_own_name(Gio.BusType.SESSION, 'org.freedesktop.Notifications', Gio.BusNameOwnerFlags.NONE, acquired, ready, None)
GLib.MainLoop().run()
