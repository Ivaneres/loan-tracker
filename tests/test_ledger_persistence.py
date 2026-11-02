"""Ledger persistence hardening: atomic writes, daily backups, no silent wipe.

Regression tests for the production data-loss incident where a partially
written / unreadable ``ledger.json`` was silently replaced by an empty default.
"""
import glob
import json
import os
import tempfile
import unittest
from unittest import mock

import app as app_mod


class LedgerPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        self.path = os.path.join(self.dir, 'ledger.json')
        self._patches = mock.patch.multiple(
            app_mod, DATA_PATH=self.path, LEDGER_BACKUP_KEEP=3,
        )
        self._patches.start()
        self.addCleanup(self._patches.stop)

    def _read(self, path=None):
        with open(path or self.path) as fh:
            return fh.read()

    def _write_raw(self, text, path=None):
        with open(path or self.path, 'w') as fh:
            fh.write(text)

    def _temp_files(self):
        return [name for name in os.listdir(self.dir) if name.endswith('.tmp')]

    def test_save_writes_valid_json_and_leaves_no_temp_files(self):
        app_mod.save_data({'loans': {}, 'users': {'a': {'spending': {}}}})
        self.assertEqual(json.loads(self._read())['users'], {'a': {'spending': {}}})
        self.assertEqual(self._temp_files(), [])

    def test_save_replaces_in_one_step_and_keeps_group_readable_mode(self):
        self._write_raw(json.dumps({'loans': {}, 'users': {}}))
        os.chmod(self.path, 0o660)
        app_mod.save_data({'loans': {}, 'users': {'a': {}}})
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o660)

    def test_load_missing_file_creates_empty_default(self):
        data = app_mod.load_data()
        self.assertEqual(data, {'loans': {}, 'users': {}})
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(json.loads(self._read()), {'loans': {}, 'users': {}})

    def test_load_corrupt_file_quarantines_and_raises_without_wiping(self):
        raw = '{"loans": {}, "users": {"ivan": {"spending": {"transactions": ['
        self._write_raw(raw)
        with self.assertRaises(app_mod.LedgerUnreadableError):
            app_mod.load_data()
        # The (corrupt) original is preserved, not overwritten with defaults.
        self.assertFalse(os.path.exists(self.path))
        quarantined = glob.glob(self.path + '.corrupt-*')
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(self._read(quarantined[0]), raw)

    def test_load_empty_file_quarantines_and_raises(self):
        self._write_raw('   \n')
        with self.assertRaises(app_mod.LedgerUnreadableError):
            app_mod.load_data()
        self.assertEqual(len(glob.glob(self.path + '.corrupt-*')), 1)

    def test_daily_backup_taken_once_per_day_and_holds_prior_state(self):
        first = {'loans': {}, 'users': {'a': {'spending': {'transactions': []}}}}
        app_mod.save_data(first)
        # First ever write has nothing to snapshot.
        self.assertEqual(app_mod._ledger_backup_paths(), [])

        second = {'loans': {}, 'users': {'a': {'spending': {'transactions': [1]}}}}
        app_mod.save_data(second)
        backups = app_mod._ledger_backup_paths()
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(self._read(backups[0])), first)

        # Same UTC day: no additional snapshot.
        app_mod.save_data({'loans': {}, 'users': {'a': {}}})
        self.assertEqual(len(app_mod._ledger_backup_paths()), 1)

    def test_backups_are_pruned_to_keep_limit(self):
        app_mod.save_data({'loans': {}, 'users': {}})
        for stamp in ('20200101T000000Z', '20200102T000000Z', '20200103T000000Z'):
            self._write_raw(
                json.dumps({'loans': {}, 'users': {}}),
                path=os.path.join(self.dir, f'ledger.json.bak-{stamp}'),
            )
        app_mod.save_data({'loans': {}, 'users': {'a': {}}})
        backups = app_mod._ledger_backup_paths()
        names = [os.path.basename(p) for p in backups]
        self.assertEqual(len(names), 3)
        # The oldest snapshot is pruned; today's newest snapshot is kept.
        self.assertFalse(any(n.endswith('20200101T000000Z') for n in names))
        today = app_mod.datetime.now(app_mod.timezone.utc).strftime('%Y%m%d')
        self.assertTrue(any(today in n for n in names))

    def test_failed_serialisation_keeps_previous_file_and_cleans_temp(self):
        app_mod.save_data({'loans': {}, 'users': {'ok': {}}})
        before = self._read()
        with self.assertRaises(TypeError):
            app_mod.save_data({'loans': {1, 2}, 'users': {}})
        self.assertEqual(self._read(), before)
        self.assertEqual(self._temp_files(), [])


if __name__ == '__main__':
    unittest.main()