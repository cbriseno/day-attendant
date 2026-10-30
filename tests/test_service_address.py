"""The local service keeps its address and session across restarts and never stays down."""
import http.client
import json
import socket
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from day_attendant import lifecycle
from day_attendant.profiles import configure
from test_daily_domain import DailyBase

ROOT = Path(__file__).resolve().parent.parent


class ServiceAddress(DailyBase):
    def setUp(self):
        super().setUp()
        configure(self.app, {'notification_policy': {'enabled': False}}, 'address-policy')
        self.addCleanup(lambda: lifecycle.stop(self.app))

    def service(self, command):
        run = subprocess.run([sys.executable, '-B', '-m', 'day_attendant', '--json', '--state', self.temp.name,
                              'service', command], cwd=ROOT, capture_output=True, text=True, timeout=90)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return json.loads(run.stdout)['data']

    def receipt(self):
        return json.loads((Path(self.temp.name) / 'ui-server.json').read_text())

    def authorized(self, origin, token):
        request = Request(origin + '/api/state', headers={'Authorization': 'Bearer ' + token, 'Origin': origin})
        with urlopen(request, timeout=10) as response: return response.status == 200

    def test_restart_keeps_address_and_open_tab_session(self):
        first = self.service('start')
        before = self.receipt()
        for _ in range(3):
            restarted = self.service('restart')
            self.assertTrue(restarted['running'], restarted)
            self.assertEqual(restarted['origin'], first['origin'])
        status = self.service('status')
        self.assertTrue(status['running'])
        self.assertEqual(status['origin'], first['origin'])
        self.assertNotEqual(status['pid'], before['pid'])
        self.assertEqual(lifecycle.session_url(self.app), first['origin'] + '/#session=' + before['token'])
        # The cookie an open tab already holds is the same session, so it keeps working.
        self.assertTrue(self.authorized(first['origin'], before['token']))
        self.assertEqual((Path(self.temp.name) / 'ui-session.json').stat().st_mode & 0o777, 0o600)

    def test_taken_port_falls_back_and_status_matches_open(self):
        first = self.service('start')
        self.service('stop')
        port = int(first['origin'].rsplit(':', 1)[1])
        blocker = socket.socket(); blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); blocker.bind(('127.0.0.1', port)); blocker.listen()
        try:
            started = self.service('start')
            self.assertNotEqual(started['origin'], first['origin'])
            self.assertEqual(self.service('status')['origin'], started['origin'])
            self.assertTrue(lifecycle.session_url(self.app).startswith(started['origin'] + '/#session='))
        finally: blocker.close()

    def test_shutdown_reply_is_complete_and_stop_waits_for_owner(self):
        self.service('start')
        record = self.receipt()
        connection = http.client.HTTPConnection(record['origin'].removeprefix('http://'), timeout=10)
        connection.request('POST', '/api/shutdown', body=b'{}', headers={
            'Authorization': 'Bearer ' + record['token'], 'Origin': record['origin'], 'Content-Type': 'application/json'})
        self.assertEqual(json.loads(connection.getresponse().read())['data'], {'stopping': True})
        connection.close()
        stopped = lifecycle.stop(self.app) if lifecycle.receipt(self.app) else {'running': False}
        self.assertFalse(stopped['running'])
        self.assertTrue(self.service('start')['running'])

    def test_truncated_shutdown_reply_still_waits_and_restart_comes_back(self):
        from unittest.mock import patch
        from day_attendant import web
        self.service('start')
        original = web.client
        def truncated(state, path='/api/state', data=None):
            result = original(state, path, data)
            if path == '/api/shutdown': raise http.client.IncompleteRead(b'', 26)
            return result
        with patch.object(web, 'client', truncated):
            self.assertFalse(lifecycle.stop(self.app)['running'])
        self.assertFalse(lifecycle.status(self.app)['running'])
        self.assertTrue(lifecycle.start(self.app)['running'])
