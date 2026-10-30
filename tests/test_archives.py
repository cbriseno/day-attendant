"""Coherent real-store backups, offline restore and maintenance failure paths."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from day_attendant import archives
from day_attendant.connected import Connected
from day_attendant.errors import AssistantError
from day_attendant.service import Assistant
from test_daily_domain import DailyBase
from test_connected import ReviewFixture,NOW

class ArchiveTests(DailyBase):
    def setUp(self):
        super().setUp();self.output=tempfile.TemporaryDirectory(prefix='day-backup-test-');self.folder=Path(self.output.name)/'backups'
    def tearDown(self):self.output.cleanup();super().tearDown()
    def test_snapshot_roundtrip_keeps_dependencies_language_relations_and_continuity(self):
        a,b=self.add('Responsabilidad humana'),self.add('Other card')
        self.apply('dependency_update',{'uuid':b,'dependsOn':[a]});self.apply('related_update',{'uuid':a,'relatedTo':[b]})
        self.apply('continuity_update',{'uuid':a,'context':'Seguir aquí','next':'Abrir borrador'})
        pending=self.waiting_retire(a)
        (self.app.store.path/'ui-server.json').write_text('PRIVATE-TOKEN')
        backup=archives.snapshot(self.app,self.folder,key='backup-once');again=archives.snapshot(self.app,self.folder,key='backup-once')
        self.assertEqual(backup,again);self.assertIsNone(self.app.store.state_value('maintenance'))
        with zipfile.ZipFile(backup['path']) as archive:self.assertNotIn('ui-server.json',archive.namelist())
        result=archives.restore(backup['path'],Path(self.output.name)/'restored')
        restored=Connected(Assistant(result['state'],clock=lambda:NOW),ReviewFixture());restored.initialize();restored.startup()
        rows={r['id']:r for r in restored.snapshot()['tasks']}
        self.assertEqual(rows[b]['dependsOn'],[a]);self.assertEqual(rows[a]['relatedTo'],[b]);self.assertEqual(rows[a]['title'],'Responsabilidad humana');self.assertEqual(rows[a]['context'],'Seguir aquí')
        self.assertNotEqual(result['profile_id'],result['source_profile_id']);self.assertFalse(result['reminders_enabled']);self.assertFalse((Path(result['state'])/'ui-server.json').exists())
        self.assertEqual(restored.proposal(pending['id'])['phase'],'stale')
    def test_failed_backup_leaves_no_published_file_and_releases_maintenance(self):
        self.add('Keep me')
        with patch.object(archives.shutil,'disk_usage',return_value=type('Space',(),{'free':0})()):
            with self.assertRaises(AssistantError) as error:archives.snapshot(self.app,self.folder)
        self.assertEqual(error.exception.code,'backup_space');self.assertEqual(list(self.folder.glob('*.zip')),[]);self.assertIsNone(self.app.store.state_value('maintenance'))
        self.add('Can continue');self.assertEqual(len(self.app.tasks.rows()),2)
    def test_tampered_backup_and_existing_target_are_rejected(self):
        self.add('Preserve');backup=archives.snapshot(self.app,self.folder)
        target=Path(self.output.name)/'occupied';target.mkdir();(target/'keep').write_text('unchanged')
        with self.assertRaises(AssistantError):archives.restore(backup['path'],target)
        self.assertEqual((target/'keep').read_text(),'unchanged')
        corrupt=Path(self.output.name)/'corrupt.zip'
        with zipfile.ZipFile(backup['path']) as src,zipfile.ZipFile(corrupt,'w') as dst:
            for name in src.namelist():dst.writestr(name,b'bad' if name=='assistant.sqlite3' else src.read(name))
        with self.assertRaises(AssistantError):archives.restore(corrupt,Path(self.output.name)/'new')
        self.assertFalse((Path(self.output.name)/'new').exists())
    def test_live_maintenance_rejects_writes_without_waiting_for_model(self):
        with archives.maintenance(self.app,'test'):
            with self.assertRaises(AssistantError) as error:self.request('task_add',{'title':'Must not appear'})
            self.assertEqual(error.exception.code,'maintenance')
        self.assertEqual(self.app.tasks.rows(),[])
    def test_schema3_upgrade_requires_exact_snapshot_and_keeps_profile_identity(self):
        uid=self.add('Pre-upgrade');profile=self.app.store.state_value('profile')
        with self.app.store.connect() as con:con.execute('PRAGMA user_version=3')
        with self.assertRaises(AssistantError):self.board.initialize()
        backup=archives.snapshot(self.app,self.folder,category='migration')
        self.board.initialize(_upgrade_backup=backup['path'])
        self.assertEqual(self.app.store.state_value('profile'),profile);self.assertEqual(self.app.tasks.get(uid)['description'],'Pre-upgrade')
        with self.app.store.connect() as con:self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0],4)

if __name__=='__main__':unittest.main()
