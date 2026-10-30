"""Effective configuration and degraded-startup regression checks."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.request import Request,urlopen
from day_attendant.codexbridge import restricted_command,CodexBridge
from day_attendant.errors import AssistantError

class BridgeTests(unittest.TestCase):
    def test_effective_codex_home_mcp_is_disabled(self):
        with tempfile.TemporaryDirectory() as home:
            Path(home,'config.toml').write_text('[mcp_servers.fixture]\ncommand="never-executed"\n')
            with patch.dict(os.environ,{'CODEX_HOME':home}):args=restricted_command('/not-executed/codex')
            self.assertIn('mcp_servers.fixture.enabled=false',args)
            self.assertIn('mcp_servers.fixture.required=false',args)

    def test_unverified_effective_configuration_is_rejected(self):
        with self.assertRaises(AssistantError) as error:CodexBridge.assert_restricted({'features':{'shell_tool':True}})
        self.assertEqual(error.exception.code,'agent_isolation_unverified')

    def test_missing_agent_still_serves_real_synthetic_state(self):
        with tempfile.TemporaryDirectory() as state:
            script="""
import sys
from unittest.mock import patch
from day_attendant.errors import AssistantError
from day_attendant.web import serve
with patch('day_attendant.web.create_harness',side_effect=AssistantError('agent_auth_required','Controlled unavailable account')):
    serve(sys.argv[1],kind='synthetic')
"""
            process=subprocess.Popen([sys.executable,'-B','-c',script,state],cwd=Path(__file__).resolve().parent.parent,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
            try:
                receipt=Path(state,'ui-server.json')
                for _ in range(150):
                    # The receipt exists a moment before its contents are written.
                    if receipt.exists() and receipt.read_text().strip():break
                    if process.poll() is not None:self.fail(process.stderr.read())
                    time.sleep(.1)
                data=json.loads(receipt.read_text())
                with urlopen(Request(data['origin']+'/api/state',headers={'Authorization':'Bearer '+data['token']}),timeout=5) as r:board=json.load(r)['data']
                self.assertFalse(board['agent']['connected']);self.assertEqual(len(board['tasks']),4)
                self.assertIn('Controlled unavailable',board['agent']['error'])
            finally:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                process.stderr.close()

if __name__=='__main__':unittest.main()
