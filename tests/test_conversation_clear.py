"""Complete current conversation, /clear and uncapped inputs, through real stores."""
import base64
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from day_attendant.attachments import save
from day_attendant.connected import Connected
from day_attendant.conversation import Conversation
from day_attendant.errors import AssistantError
from day_attendant.service import Assistant
from day_attendant.web import LocalServer
from test_connected import NOW,ReviewFixture

ROOT=Path(__file__).resolve().parent.parent


def png(size=0,width=4,height=3):
    return b'\x89PNG\r\n\x1a\n'+b'\x00\x00\x00\rIHDR'+struct.pack('>II',width,height)+b'\x00'*size


class RecordingChat(ReviewFixture):
    """Answers chat turns with no intent and records what each native turn received."""
    def __init__(self):self.threads=[];self.prompts=[];self.images=[]
    def thread(self,previous=None):
        self.threads.append(previous);return 'native-'+str(len(self.threads))
    def turn(self,thread,prompt,schema,**kwargs):
        if 'intent' not in schema['properties']:return super().turn(thread,prompt,schema,**kwargs)
        self.prompts.append(json.loads(prompt.split('Selected state and portable continuity:\n',1)[1]));self.images.append(kwargs.get('images',[]))
        return {'output':{'message':'Noted.','uncertainties':[],'intent':None,'additionalIntents':[],'unprocessed':[]},
                'model':self.model,'thread_id':thread,'turn_id':'turn-'+str(len(self.prompts)),'seconds':0,'status':'completed'}


class ConversationClearTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='day-clear-test-')
        self.app=Assistant(self.temp.name,clock=lambda:NOW);self.fixture=RecordingChat()
        self.board=Connected(self.app,self.fixture);self.board.initialize(kind='personal',zone='America/Edmonton')
        self.chat=Conversation(self.board)
    def tearDown(self):self.temp.cleanup()
    def say(self,text,key,images=None):
        self.chat.submit(text,key,images);self.chat.run(key);return self.fixture.prompts[-1]['continuity']
    def stored(self):
        with self.app.store.connect() as con:return con.execute('SELECT count(*) FROM messages').fetchone()[0]

    def test_complete_conversation_is_sent_untruncated_in_fresh_sessions(self):
        long=['Message '+str(n)+' '+'x'*6000 for n in range(30)]
        for n,text in enumerate(long):self.say(text,'long-'+str(n))
        continuity=self.say('And now?','last')
        human=[m['text'] for m in continuity['messages'] if m['role']=='user']
        self.assertEqual(human,long)
        self.assertEqual(len(continuity['messages']),60)
        # No native session is ever resumed; the profile supplies the conversation.
        self.assertEqual(len(self.fixture.prompts),31);self.assertTrue(all(previous is None for previous in self.fixture.threads))
        self.assertEqual(self.app.store.state_value('chat_threads'),None)

    def test_clear_starts_a_new_conversation_and_keeps_durable_state(self):
        before=len(self.board.snapshot()['tasks'])
        self.say('Remember that the photography idea is only context.','before')
        cleared=self.chat.clear('clear-1')
        self.assertEqual(cleared['status'],'cleared')
        self.assertEqual(self.board.snapshot()['messages'],[])
        self.assertEqual(self.board.snapshot()['conversation']['clearedAt'],cleared['clearedAt'])
        continuity=self.say('What do I have?','after')
        self.assertEqual(continuity['messages'],[])
        self.assertNotIn('photography',json.dumps(self.fixture.prompts[-1]))
        self.assertEqual([m['key'] for m in self.board.snapshot()['messages']],['after','after:assistant'])
        self.assertEqual(len(self.board.snapshot()['tasks']),before)
        self.assertEqual(self.stored(),4)
        self.assertEqual(set(continuity),{'messages','openQuestions','scope','authority'})

    def restart(self):
        board=Connected(Assistant(self.temp.name,clock=lambda:NOW),self.fixture);board.initialize(kind='personal');board.startup()
        return board

    def test_a_profile_without_a_conversation_starts_clean_once(self):
        self.say('Stored before the upgrade.','before-upgrade')
        self.assertEqual([m['key'] for m in self.restart().snapshot()['messages']],['before-upgrade','before-upgrade:assistant'])
        # A profile from before /clear existed has stored messages and no conversation.
        with self.app.store.connect() as con:con.execute("DELETE FROM state WHERE key='conversation'")
        upgraded=self.restart()
        self.assertEqual(upgraded.snapshot()['messages'],[])
        self.assertEqual(upgraded.snapshot()['conversation']['clearedAt'],upgraded.app.now())
        chat=Conversation(upgraded);chat.submit('What do I have?','after-upgrade');chat.run('after-upgrade')
        self.assertEqual(self.fixture.prompts[-1]['continuity']['messages'],[])
        self.assertEqual(self.stored(),4)
        self.assertEqual([m['key'] for m in self.restart().snapshot()['messages']],['after-upgrade','after-upgrade:assistant'])

    def test_clear_is_idempotent_and_slash_clear_is_the_same_action(self):
        self.say('First conversation.','one')
        first=self.chat.clear('clear-same')
        self.say('Second conversation.','two')
        self.assertEqual(self.chat.clear('clear-same'),first)
        self.assertEqual([m['key'] for m in self.board.snapshot()['messages']],['two','two:assistant'])
        typed=self.chat.submit('  /clear ','typed-clear')
        self.assertEqual(typed['status'],'cleared');self.assertEqual(self.board.snapshot()['messages'],[])
        with self.assertRaises(AssistantError) as error:self.chat.submit('/clear','clear-image',['any-image'])
        self.assertEqual(error.exception.code,'invalid_message')

    def test_clear_waits_for_a_pending_reply(self):
        self.chat.submit('Still being answered.','pending')
        with self.assertRaises(AssistantError) as error:self.chat.clear('too-early')
        self.assertEqual(error.exception.code,'chat_busy')
        self.chat.run('pending');self.assertEqual(self.chat.clear('after-reply')['status'],'cleared')

    def test_long_messages_many_and_large_images_are_accepted(self):
        text='Capture these. '+'y'*60000
        refs=[save(self.app,'shot-'+str(n)+'.png','image/png',base64.b64encode(png(16+n)).decode(),'image-'+str(n))['id'] for n in range(6)]
        large=save(self.app,'large.png','image/png',base64.b64encode(png(11*1024*1024)).decode(),'image-large')
        refs.append(large['id'])
        self.say(text,'many-images',refs)
        self.assertEqual(len(self.fixture.images[-1]),7)
        self.assertEqual(self.board.snapshot()['messages'][0]['text'],text)


class ClearOverHttpAndCli(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='day-clear-http-')
        self.app=Assistant(self.temp.name);Connected(self.app).initialize()
        self.server=LocalServer(self.app,RecordingChat())
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        Path(self.temp.name,'ui-server.json').write_text(json.dumps({'origin':self.server.origin,'token':self.server.token,'pid':0}))
    def tearDown(self):
        self.server.shutdown();self.thread.join(5);self.server.pool.shutdown(wait=True);self.server.server_close();self.temp.cleanup()
    def cli(self,*args):
        run=subprocess.run([sys.executable,'-B',str(ROOT/'day-attendant.py'),'--state',self.temp.name,'--json',*args],capture_output=True,text=True,timeout=60)
        return json.loads(run.stdout)

    def test_cli_chat_and_clear_share_the_running_service(self):
        long='z'*70000
        sent=self.cli('board','chat',long,'--key','cli-long')
        self.assertTrue(sent['ok'],sent)
        deadline=time.monotonic()+30
        while [m['key'] for m in self.cli('board','list')['data']['messages']]!=['cli-long','cli-long:assistant']:
            self.assertLess(time.monotonic(),deadline);time.sleep(0.2)
        self.assertEqual(self.cli('board','list')['data']['messages'][0]['text'],long)
        cleared=self.cli('board','clear','--key','cli-clear')
        self.assertTrue(cleared['ok'],cleared);self.assertEqual(cleared['data']['status'],'cleared')
        self.assertEqual(self.cli('board','list')['data']['messages'],[])
        self.assertEqual(self.cli('board','clear','--key','cli-clear')['data'],cleared['data'])


if __name__=='__main__':unittest.main()
