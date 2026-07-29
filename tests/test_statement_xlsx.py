"""Excel (.xlsx) statement upload: conversion + local tabular parse + file-picker accept attrs."""
import io
import time
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

import app as app_mod

ROOT = Path(__file__).resolve().parents[1]


def _revolut_like_xlsx_bytes(*, rows: int = 2, include_pending: bool = False) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = 'Account'
    ws.append(
        [
            'Type',
            'Product',
            'Started Date',
            'Completed Date',
            'Description',
            'Amount',
            'Fee',
            'Currency',
            'State',
            'Balance',
        ]
    )
    samples = [
        (
            'CARD_PAYMENT',
            'Current',
            '2024-06-30 23:10:00',
            '2024-07-01 08:02:00',
            'REVOLUT*COFFEE',
            -4.8,
            0,
            'GBP',
            'COMPLETED',
            120.5,
        ),
        (
            'CARD_PAYMENT',
            'Current',
            '2024-07-10 12:00:00',
            '2024-07-10 12:00:00',
            'SUPERMARKET',
            -32.15,
            0,
            'GBP',
            'COMPLETED',
            88.35,
        ),
        (
            'TOPUP',
            'Current',
            '2024-07-11 09:00:00',
            '2024-07-11 09:00:00',
            'Payment from JOHN',
            50.0,
            0,
            'GBP',
            'COMPLETED',
            138.35,
        ),
    ]
    for i in range(rows):
        base = list(samples[i % len(samples)])
        # Unique descriptions for large fixtures
        if rows > len(samples):
            base[4] = f'{base[4]} #{i + 1}'
            day = 1 + (i % 28)
            base[2] = f'2024-07-{day:02d} 12:00:00'
            base[3] = f'2024-07-{day:02d} 12:00:00'
        ws.append(base)
    if include_pending:
        ws.append(
            [
                'CARD_PAYMENT',
                'Current',
                '2024-07-15 10:00:00',
                '',
                'PENDING MERCHANT',
                -9.99,
                0,
                'GBP',
                'PENDING',
                128.36,
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestSpreadsheetHelpers(unittest.TestCase):
    def test_is_spreadsheet_filename(self):
        self.assertTrue(app_mod._is_spreadsheet_filename('Revolut-statement.XLSX'))
        self.assertTrue(app_mod._is_spreadsheet_filename('a.xlsm'))
        self.assertTrue(app_mod._is_spreadsheet_filename('legacy.xls'))
        self.assertFalse(app_mod._is_spreadsheet_filename('a.csv'))
        self.assertFalse(app_mod._is_spreadsheet_filename('a.pdf'))

    def test_extract_xlsx_to_csv_like_text(self):
        raw = _revolut_like_xlsx_bytes()
        text = app_mod._extract_spreadsheet_text(raw, 'revolut.xlsx')
        self.assertIn('Started Date,Completed Date,Description,Amount', text)
        self.assertIn('REVOLUT*COFFEE', text)
        self.assertIn('-4.8', text)
        self.assertIn('SUPERMARKET', text)
        self.assertIn('-32.15', text)

    def test_legacy_xls_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            app_mod._extract_spreadsheet_text(b'not-a-real-xls', 'old.xls')
        self.assertIn('.xlsx', str(ctx.exception))

    def test_spending_pipeline_from_xlsx(self):
        raw = _revolut_like_xlsx_bytes()
        text, hints, pipeline = app_mod._spending_text_hints_pipeline_from_raw('revolut.xlsx', raw)
        self.assertIn('REVOLUT*COFFEE', text)
        self.assertEqual(hints, [])
        self.assertEqual(pipeline['source_format'], 'xlsx')
        self.assertGreater(pipeline['lengths']['extracted_text'], 40)

    def test_iter_prepare_emits_spreadsheet_step(self):
        raw = _revolut_like_xlsx_bytes()
        events = list(app_mod._iter_prepare_spending_raw('statement.xlsx', raw))
        steps = [e.get('step') for e in events if e.get('type') == 'progress']
        self.assertIn('decode_spreadsheet', steps)
        complete = [e for e in events if e.get('type') == 'prep_complete']
        self.assertEqual(len(complete), 1)
        text, _trunc, _hints, pipeline = complete[0]['prep']
        self.assertIn('SUPERMARKET', text)
        self.assertEqual(pipeline['source_format'], 'xlsx')


class TestTabularLocalParse(unittest.TestCase):
    def test_revolut_xlsx_parses_without_llm(self):
        raw = _revolut_like_xlsx_bytes(rows=3, include_pending=True)
        text = app_mod._extract_spreadsheet_text(raw, 'revolut.xlsx')
        parsed = app_mod._try_parse_tabular_spending_transactions(text)
        self.assertIsNotNone(parsed)
        rows, meta = parsed
        self.assertEqual(meta['mode'], 'tabular')
        self.assertEqual(meta['profile'], 'revolut_like')
        self.assertEqual(len(rows), 3)  # pending skipped
        coffee = next(r for r in rows if 'COFFEE' in r['description'])
        self.assertEqual(coffee['direction'], 'outgoing')
        self.assertEqual(coffee['amount'], 4.8)
        self.assertEqual(coffee['completed_date'], '2024-07-01 08:02:00')
        self.assertEqual(coffee['started_date'], '2024-06-30 23:10:00')
        topup = next(r for r in rows if 'JOHN' in r['description'])
        self.assertEqual(topup['direction'], 'incoming')
        self.assertEqual(topup['amount'], 50.0)

    def test_iter_extraction_uses_tabular_not_llm(self):
        raw = _revolut_like_xlsx_bytes()
        text = app_mod._extract_spreadsheet_text(raw, 'revolut.xlsx')
        with mock.patch.object(app_mod, '_iter_extract_spending_transactions_llm') as llm:
            events = list(app_mod.iter_spending_transaction_extraction(text))
            llm.assert_not_called()
        steps = [e.get('step') for e in events if e.get('type') == 'progress']
        self.assertIn('tabular_parse', steps)
        result = next(e for e in events if e.get('type') == 'result')
        self.assertEqual(result['meta']['mode'], 'tabular')
        self.assertGreaterEqual(len(result['rows']), 2)

    def test_large_revolut_parse_is_fast(self):
        raw = _revolut_like_xlsx_bytes(rows=800)
        text = app_mod._extract_spreadsheet_text(raw, 'revolut.xlsx')
        t0 = time.perf_counter()
        parsed = app_mod._try_parse_tabular_spending_transactions(text)
        elapsed = time.perf_counter() - t0
        self.assertIsNotNone(parsed)
        rows, _meta = parsed
        self.assertEqual(len(rows), 800)
        # Local parse should be well under a second even for a busy month.
        self.assertLess(elapsed, 2.0)

    def test_normalize_preserves_revolut_dates(self):
        raw = _revolut_like_xlsx_bytes(rows=1)
        text = app_mod._extract_spreadsheet_text(raw, 'revolut.xlsx')
        rows, _ = app_mod._try_parse_tabular_spending_transactions(text)
        normalized = app_mod._normalize_spending_transactions(rows)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]['date'], '2024-07-01')
        self.assertEqual(normalized[0]['started_date'], '2024-06-30')
        self.assertEqual(normalized[0]['completed_date'], '2024-07-01')

    def test_money_in_out_csv(self):
        text = (
            'Date,Description,Paid in,Paid out\n'
            '01/07/2024,Salary,2000.00,\n'
            '02/07/2024,Rent,,850.00\n'
        )
        parsed = app_mod._try_parse_tabular_spending_transactions(text)
        self.assertIsNotNone(parsed)
        rows, meta = parsed
        self.assertEqual(meta['profile'], 'money_columns')
        self.assertEqual(len(rows), 2)
        by_desc = {r['description']: r for r in rows}
        self.assertEqual(by_desc['Salary']['direction'], 'incoming')
        self.assertEqual(by_desc['Salary']['amount'], 2000.0)
        self.assertEqual(by_desc['Rent']['direction'], 'outgoing')
        self.assertEqual(by_desc['Rent']['amount'], 850.0)

    def test_llm_chunk_split(self):
        lines = [f'row {i},value\n' for i in range(100)]
        text = ''.join(lines)
        chunks = app_mod._split_statement_text_into_llm_chunks(text, max_chars=80)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(''.join(chunks), text)
        for c in chunks:
            self.assertLessEqual(len(c), 80 + max(len(l) for l in lines))

    def test_unstructured_text_does_not_tabular_parse(self):
        text = 'This is a scanned PDF dump without headers\n01 Jul Coffee 4.80\n'
        self.assertIsNone(app_mod._try_parse_tabular_spending_transactions(text))


class TestStatementXlsxUiAccept(unittest.TestCase):
    def setUp(self):
        self.client = app_mod.app.test_client()
        self.data = {'users': {'ivan': {'spending': {}}}, 'loans': {}}

    def _login(self):
        with self.client.session_transaction() as sess:
            sess['username'] = 'ivan'

    @mock.patch.object(app_mod, 'save_data')
    @mock.patch.object(app_mod, 'load_data')
    def test_home_file_input_accepts_xlsx(self, load_mock, save_mock):
        load_mock.return_value = self.data
        self._login()
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn('id="spending-file"', html)
        self.assertIn('.xlsx', html)
        self.assertIn(
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            html,
        )
        self.assertIn('Excel (.xlsx)', html)

    def test_templates_and_loan_page_accept_xlsx(self):
        home = (ROOT / 'templates' / 'home.html').read_text(encoding='utf-8')
        index = (ROOT / 'templates' / 'index.html').read_text(encoding='utf-8')
        for html in (home, index):
            self.assertIn('.xlsx', html)
            self.assertIn(
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                html,
            )


if __name__ == '__main__':
    unittest.main()
