"""Excel (.xlsx) statement upload: conversion + file-picker accept attrs."""
import io
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import Workbook

import app as app_mod

ROOT = Path(__file__).resolve().parents[1]


def _revolut_like_xlsx_bytes() -> bytes:
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
    ws.append(
        [
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
        ]
    )
    ws.append(
        [
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
