"""Unit tests for Daily Budget bill-pull diff helpers (Node-loadable JS)."""
import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIFF_JS = ROOT / 'static' / 'db_bill_diff.js'


def _node_eval(expr: str):
    script = f"""
const api = require({json.dumps(str(DIFF_JS))});
const result = ({expr});
process.stdout.write(JSON.stringify(result));
"""
    proc = subprocess.run(
        ['node', '-e', script],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or 'node failed')
    return json.loads(proc.stdout)


class TestBillPullDiff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            subprocess.run(['node', '-v'], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            raise unittest.SkipTest(f'node required: {e}') from e

    def test_normalize_label(self):
        self.assertEqual(
            _node_eval('api.normalizeBillLabel("SPOTIFY LDN!")'),
            'spotify ldn',
        )

    def test_diff_new_changed_removed_same(self):
        expr = """
(() => {
  const prev = [
    { id: '1', label: 'Rent', amount: 1000, included: true },
    { id: '2', label: 'Spotify', amount: 10.99, included: true },
    { id: '3', label: 'Gym', amount: 30, included: false },
  ];
  const next = [
    { id: 'a', label: 'RENT', amount: 1050, source: 'category' },
    { id: 'b', label: 'Spotify', amount: 10.99, source: 'subscription_signal' },
    { id: 'c', label: 'Netflix', amount: 15.99, source: 'subscription_signal' },
  ];
  return api.diffBillPull(prev, next);
})()
"""
        out = _node_eval(expr)
        kinds = {b['label'].lower(): b['changeKind'] for b in out['items']}
        self.assertEqual(kinds['rent'], 'changed')
        self.assertEqual(kinds['spotify'], 'same')
        self.assertEqual(kinds['netflix'], 'new')
        rent = next(b for b in out['items'] if b['label'].lower() == 'rent')
        self.assertEqual(rent['previousAmount'], 1000)
        spotify = next(b for b in out['items'] if b['label'].lower() == 'spotify')
        self.assertEqual(spotify.get('included'), True)
        self.assertEqual(len(out['removed']), 1)
        self.assertIn('gym', out['removed'][0]['label'].lower())
        self.assertEqual(out['summary']['newCount'], 1)
        self.assertEqual(out['summary']['changedCount'], 1)
        self.assertEqual(out['summary']['sameCount'], 1)
        self.assertEqual(out['summary']['removedCount'], 1)
        self.assertTrue(out['summary']['hadBaseline'])

    def test_first_pull_no_baseline_summary(self):
        out = _node_eval(
            'api.diffBillPull([], [{label:"Spotify",amount:10}])'
        )
        self.assertFalse(out['summary']['hadBaseline'])
        self.assertEqual(
            _node_eval('api.formatBillDiffSummary(' + json.dumps(out['summary']) + ')'),
            '',
        )

    def test_format_summary(self):
        text = _node_eval(
            """api.formatBillDiffSummary({
              hadBaseline: true, newCount: 2, changedCount: 1, sameCount: 3, removedCount: 1
            })"""
        )
        self.assertIn('2 new', text)
        self.assertIn('1 changed', text)
        self.assertIn('3 unchanged', text)
        self.assertIn('1 removed', text)


class TestBillPullDiffUiMarkup(unittest.TestCase):
    def test_page_includes_diff_hooks(self):
        from unittest import mock
        import app as app_mod

        client = app_mod.app.test_client()
        data = {'users': {'ivan': {'spending': {}}}, 'loans': {}}
        with mock.patch.object(app_mod, 'load_data', return_value=data):
            with mock.patch.object(app_mod, 'save_data'):
                with client.session_transaction() as sess:
                    sess['username'] = 'ivan'
                resp = client.get('/spending/daily')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="plan-bill-diff-summary"', html)
        self.assertIn('id="plan-bill-removed-wrap"', html)
        self.assertIn('id="plan-bill-removed"', html)
        self.assertIn('db_bill_diff.js', html)
        self.assertIn('Removed by this pull', html)

        js = (ROOT / 'static' / 'daily_budget.js').read_text(encoding='utf-8')
        self.assertIn('diffBillPull', js)
        self.assertIn('renderRemovedBillList', js)
        self.assertIn('db-bill-item--new', js)
        self.assertIn('db-bill-item--changed', js)


if __name__ == '__main__':
    unittest.main()
