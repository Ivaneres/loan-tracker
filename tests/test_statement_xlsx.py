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
    def setUp(self):
        app_mod._TABULAR_HEADER_MAP_CACHE.clear()

    def test_revolut_xlsx_parses_without_llm(self):
        raw = _revolut_like_xlsx_bytes(rows=3, include_pending=True)
        text = app_mod._extract_spreadsheet_text(raw, 'revolut.xlsx')
        parsed = app_mod._try_parse_tabular_spending_transactions(text, allow_llm=False)
        self.assertIsNotNone(parsed)
        rows, meta = parsed
        self.assertEqual(meta['mode'], 'tabular')
        self.assertEqual(meta['profile'], 'revolut_like')
        self.assertEqual(meta['header_map_source'], 'alias')
        self.assertEqual(len(rows), 3)  # pending skipped
        coffee = next(r for r in rows if 'COFFEE' in r['description'])
        self.assertEqual(coffee['direction'], 'outgoing')
        self.assertEqual(coffee['amount'], 4.8)
        self.assertEqual(coffee['completed_date'], '2024-07-01 08:02:00')
        self.assertEqual(coffee['started_date'], '2024-06-30 23:10:00')
        topup = next(r for r in rows if 'JOHN' in r['description'])
        self.assertEqual(topup['direction'], 'incoming')
        self.assertEqual(topup['amount'], 50.0)

    def test_iter_extraction_uses_tabular_not_full_llm(self):
        raw = _revolut_like_xlsx_bytes()
        text = app_mod._extract_spreadsheet_text(raw, 'revolut.xlsx')
        with mock.patch.object(app_mod, '_iter_extract_spending_transactions_llm') as full_llm:
            with mock.patch.object(app_mod, '_llm_map_tabular_headers') as header_llm:
                events = list(app_mod.iter_spending_transaction_extraction(text))
                full_llm.assert_not_called()
                header_llm.assert_not_called()  # alias fast path
        steps = [e.get('step') for e in events if e.get('type') == 'progress']
        self.assertIn('tabular_parse', steps)
        result = next(e for e in events if e.get('type') == 'result')
        self.assertEqual(result['meta']['mode'], 'tabular')
        self.assertEqual(result['meta']['header_map_source'], 'alias')
        self.assertGreaterEqual(len(result['rows']), 2)

    def test_unknown_headers_use_tiny_llm_then_local_rows(self):
        text = (
            'Txn when,What happened,Cash delta,Settle flag\n'
            '2024-07-01,Coffee shop,-4.80,COMPLETED\n'
            '2024-07-02,Payroll,1000.00,COMPLETED\n'
        )
        # Without LLM mapping, unfamiliar headers fail.
        self.assertIsNone(
            app_mod._try_parse_tabular_spending_transactions(text, allow_llm=False)
        )

        def fake_llm(header_row):
            self.assertEqual(header_row[0], 'Txn when')
            return {
                'columns': {
                    'date': 0,
                    'description': 1,
                    'amount': 2,
                    'state': 3,
                },
                'amount_sign': 'negative_is_outgoing',
            }

        with mock.patch.object(app_mod, '_llm_map_tabular_headers', side_effect=fake_llm):
            with mock.patch.object(app_mod, '_iter_extract_spending_transactions_llm') as full_llm:
                events = list(app_mod.iter_spending_transaction_extraction(text))
                full_llm.assert_not_called()
        steps = [e.get('step') for e in events if e.get('type') == 'progress']
        self.assertIn('tabular_headers', steps)
        self.assertIn('tabular_parse', steps)
        result = next(e for e in events if e.get('type') == 'result')
        self.assertEqual(result['meta']['header_map_source'], 'llm_headers')
        self.assertEqual(len(result['rows']), 2)
        by_desc = {r['description']: r for r in result['rows']}
        self.assertEqual(by_desc['Coffee shop']['direction'], 'outgoing')
        self.assertEqual(by_desc['Coffee shop']['amount'], 4.8)
        self.assertEqual(by_desc['Payroll']['direction'], 'incoming')

    def test_llm_header_map_cached_across_calls(self):
        text = (
            'Txn when,What happened,Cash delta\n'
            '2024-07-01,A,-1.00\n'
            '2024-07-02,B,-2.00\n'
        )
        calls = {'n': 0}

        def fake_llm(header_row):
            calls['n'] += 1
            return {
                'columns': {'date': 0, 'description': 1, 'amount': 2},
                'amount_sign': 'negative_is_outgoing',
            }

        with mock.patch.object(app_mod, '_llm_map_tabular_headers', side_effect=fake_llm):
            first = app_mod._try_parse_tabular_spending_transactions(text)
            second = app_mod._try_parse_tabular_spending_transactions(text)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(calls['n'], 1)

    def test_large_revolut_parse_is_fast(self):
        raw = _revolut_like_xlsx_bytes(rows=800)
        text = app_mod._extract_spreadsheet_text(raw, 'revolut.xlsx')
        t0 = time.perf_counter()
        parsed = app_mod._try_parse_tabular_spending_transactions(text, allow_llm=False)
        elapsed = time.perf_counter() - t0
        self.assertIsNotNone(parsed)
        rows, _meta = parsed
        self.assertEqual(len(rows), 800)
        # Local parse should be well under a second even for a busy month.
        self.assertLess(elapsed, 2.0)

    def test_normalize_preserves_revolut_dates(self):
        raw = _revolut_like_xlsx_bytes(rows=1)
        text = app_mod._extract_spreadsheet_text(raw, 'revolut.xlsx')
        rows, _ = app_mod._try_parse_tabular_spending_transactions(text, allow_llm=False)
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
        parsed = app_mod._try_parse_tabular_spending_transactions(text, allow_llm=False)
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

    def test_positive_is_outgoing_sign_from_llm(self):
        text = (
            'Booking,Narration,Amt\n'
            '2024-07-01,Shop,12.50\n'
            '2024-07-02,Refund,-3.00\n'
        )

        def fake_llm(header_row):
            return {
                'columns': {'date': 0, 'description': 1, 'amount': 2},
                'amount_sign': 'positive_is_outgoing',
            }

        with mock.patch.object(app_mod, '_llm_map_tabular_headers', side_effect=fake_llm):
            parsed = app_mod._try_parse_tabular_spending_transactions(text)
        self.assertIsNotNone(parsed)
        rows, _ = parsed
        by_desc = {r['description']: r for r in rows}
        self.assertEqual(by_desc['Shop']['direction'], 'outgoing')
        self.assertEqual(by_desc['Refund']['direction'], 'incoming')

    def test_amex_csv_positive_charges_are_outgoing(self):
        """Amex Date/Description/Amount CSVs use positive amounts for purchases."""
        text = (
            'Date,Description,Amount\n'
            '31/07/2026,GO AHEAD GROUP          LONDON,36.60\n'
            "31/07/2026,SAINSBURY'S SUPERMARKET CAMBRIDGE,19.55\n"
        )
        parsed = app_mod._try_parse_tabular_spending_transactions(text, allow_llm=False)
        self.assertIsNotNone(parsed)
        rows, meta = parsed
        self.assertEqual(meta['header_map_source'], 'alias')
        self.assertEqual(meta['amount_sign'], 'positive_is_outgoing')
        self.assertEqual(len(rows), 2)
        by_desc = {r['description']: r for r in rows}
        go = by_desc['GO AHEAD GROUP          LONDON']
        self.assertEqual(go['direction'], 'outgoing')
        self.assertEqual(go['amount'], 36.6)
        sains = by_desc["SAINSBURY'S SUPERMARKET CAMBRIDGE"]
        self.assertEqual(sains['direction'], 'outgoing')
        self.assertEqual(sains['amount'], 19.55)

    def test_amex_csv_payment_credit_is_incoming(self):
        text = (
            'Date,Description,Amount\n'
            '01/08/2026,TESCO STORES,24.99\n'
            '02/08/2026,PAYMENT RECEIVED - THANK YOU,-150.00\n'
            '03/08/2026,COFFEE SHOP,4.50\n'
        )
        parsed = app_mod._try_parse_tabular_spending_transactions(text, allow_llm=False)
        self.assertIsNotNone(parsed)
        rows, meta = parsed
        self.assertEqual(meta['amount_sign'], 'positive_is_outgoing')
        by_desc = {r['description']: r for r in rows}
        self.assertEqual(by_desc['TESCO STORES']['direction'], 'outgoing')
        self.assertEqual(by_desc['PAYMENT RECEIVED - THANK YOU']['direction'], 'incoming')
        self.assertEqual(by_desc['PAYMENT RECEIVED - THANK YOU']['amount'], 150.0)
        self.assertEqual(by_desc['COFFEE SHOP']['direction'], 'outgoing')

    def test_amex_headers_do_not_poison_revolut_sign_cache(self):
        """Same generic headers must not lock the opposite bank into a wrong sign."""
        amex = (
            'Date,Description,Amount\n'
            '31/07/2026,SAINSBURY,19.55\n'
            '31/07/2026,BUS,36.60\n'
        )
        accounting = (
            'Date,Description,Amount\n'
            '2024-07-01,Coffee,-4.80\n'
            '2024-07-02,Payroll,1000.00\n'
        )
        amex_parsed = app_mod._try_parse_tabular_spending_transactions(amex, allow_llm=False)
        acct_parsed = app_mod._try_parse_tabular_spending_transactions(accounting, allow_llm=False)
        self.assertIsNotNone(amex_parsed)
        self.assertIsNotNone(acct_parsed)
        self.assertEqual(amex_parsed[1]['amount_sign'], 'positive_is_outgoing')
        self.assertEqual(acct_parsed[1]['amount_sign'], 'negative_is_outgoing')
        self.assertEqual(amex_parsed[0][0]['direction'], 'outgoing')
        coffee = next(r for r in acct_parsed[0] if r['description'] == 'Coffee')
        self.assertEqual(coffee['direction'], 'outgoing')
        payroll = next(r for r in acct_parsed[0] if r['description'] == 'Payroll')
        self.assertEqual(payroll['direction'], 'incoming')


class TestAmountSignInferenceMatrix(unittest.TestCase):
    """Variety of bank/card CSV shapes — majority Amount sign drives direction."""

    def setUp(self):
        app_mod._TABULAR_HEADER_MAP_CACHE.clear()

    def _parse(self, text: str):
        return app_mod._try_parse_tabular_spending_transactions(text, allow_llm=False)

    def _dirs(self, rows):
        return {r['description']: (r['direction'], r['amount']) for r in rows}

    def test_user_reported_amex_two_row_sample(self):
        text = (
            'Date,Description,Amount\n'
            '31/07/2026,GO AHEAD GROUP          LONDON,36.60\n'
            "31/07/2026,SAINSBURY'S SUPERMARKET CAMBRIDGE,19.55\n"
        )
        rows, meta = self._parse(text)
        self.assertEqual(meta['amount_sign'], 'positive_is_outgoing')
        d = self._dirs(rows)
        self.assertEqual(d['GO AHEAD GROUP          LONDON'], ('outgoing', 36.6))
        self.assertEqual(d["SAINSBURY'S SUPERMARKET CAMBRIDGE"], ('outgoing', 19.55))

    def test_amex_with_currency_symbol_and_commas(self):
        text = (
            'Date,Description,Amount\n'
            '01/07/2026,WAITROSE,£12.40\n'
            '02/07/2026,AMAZON.CO.UK,"1,234.56"\n'
            '03/07/2026,PAYMENT RECEIVED,-£500.00\n'
        )
        rows, meta = self._parse(text)
        self.assertEqual(meta['amount_sign'], 'positive_is_outgoing')
        d = self._dirs(rows)
        self.assertEqual(d['WAITROSE'][0], 'outgoing')
        self.assertEqual(d['AMAZON.CO.UK'], ('outgoing', 1234.56))
        self.assertEqual(d['PAYMENT RECEIVED'], ('incoming', 500.0))

    def test_amex_paren_negative_credit(self):
        text = (
            'Date,Description,Amount\n'
            '10/07/2026,SHELL PETROL,45.00\n'
            '11/07/2026,MERCHANT REFUND,(12.00)\n'
            '12/07/2026,UBER TRIP,8.20\n'
        )
        rows, meta = self._parse(text)
        self.assertEqual(meta['amount_sign'], 'positive_is_outgoing')
        d = self._dirs(rows)
        self.assertEqual(d['SHELL PETROL'][0], 'outgoing')
        self.assertEqual(d['MERCHANT REFUND'], ('incoming', 12.0))
        self.assertEqual(d['UBER TRIP'][0], 'outgoing')

    def test_amex_single_positive_charge(self):
        text = 'Date,Description,Amount\n15/07/2026,PRET A MANGER,6.75\n'
        rows, meta = self._parse(text)
        self.assertEqual(meta['amount_sign'], 'positive_is_outgoing')
        self.assertEqual(rows[0]['direction'], 'outgoing')
        self.assertEqual(rows[0]['amount'], 6.75)

    def test_amex_card_member_extra_columns(self):
        text = (
            'Date,Description,Card Member,Account #,Amount\n'
            '20/07/2026,TESCO STORES 2897,IVAN E,XXXX-1234,28.91\n'
            '21/07/2026,TFL TRAVEL CHARGE,IVAN E,XXXX-1234,3.50\n'
            '22/07/2026,PAYMENT RECEIVED - THANK YOU,IVAN E,XXXX-1234,-200.00\n'
        )
        # Extra columns still alias-map Date/Description/Amount.
        rows, meta = self._parse(text)
        self.assertIsNotNone(rows)
        self.assertEqual(meta['amount_sign'], 'positive_is_outgoing')
        d = self._dirs(rows)
        self.assertEqual(d['TESCO STORES 2897'][0], 'outgoing')
        self.assertEqual(d['TFL TRAVEL CHARGE'][0], 'outgoing')
        self.assertEqual(d['PAYMENT RECEIVED - THANK YOU'][0], 'incoming')

    def test_revolut_majority_negative_spend(self):
        text = (
            'Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,State,Balance\n'
            'CARD_PAYMENT,Current,2026-07-01 09:00:00,2026-07-01 09:05:00,REVOLUT*COFFEE,-4.80,0,GBP,COMPLETED,100\n'
            'CARD_PAYMENT,Current,2026-07-02 12:00:00,2026-07-02 12:00:00,SUPERMARKET,-32.15,0,GBP,COMPLETED,67.85\n'
            'TOPUP,Current,2026-07-03 08:00:00,2026-07-03 08:00:00,TOPUP JOHN,50.00,0,GBP,COMPLETED,117.85\n'
        )
        rows, meta = self._parse(text)
        self.assertEqual(meta['amount_sign'], 'negative_is_outgoing')
        d = self._dirs(rows)
        self.assertEqual(d['REVOLUT*COFFEE'], ('outgoing', 4.8))
        self.assertEqual(d['SUPERMARKET'], ('outgoing', 32.15))
        self.assertEqual(d['TOPUP JOHN'], ('incoming', 50.0))

    def test_accounting_style_generic_headers(self):
        text = (
            'Date,Description,Amount\n'
            '2026-07-01,Coffee,-4.80\n'
            '2026-07-02,Rent,-850.00\n'
            '2026-07-03,Salary,2800.00\n'
            '2026-07-04,Bus,-2.50\n'
        )
        rows, meta = self._parse(text)
        self.assertEqual(meta['amount_sign'], 'negative_is_outgoing')
        d = self._dirs(rows)
        self.assertEqual(d['Coffee'][0], 'outgoing')
        self.assertEqual(d['Rent'][0], 'outgoing')
        self.assertEqual(d['Salary'][0], 'incoming')
        self.assertEqual(d['Bus'][0], 'outgoing')

    def test_money_in_out_columns_unaffected(self):
        text = (
            'Date,Description,Paid in,Paid out\n'
            '01/07/2026,Salary,2000.00,\n'
            '02/07/2026,Groceries,,45.20\n'
            '03/07/2026,Refund,12.00,\n'
        )
        rows, meta = self._parse(text)
        self.assertEqual(meta['amount_sign'], 'absolute')
        d = self._dirs(rows)
        self.assertEqual(d['Salary'], ('incoming', 2000.0))
        self.assertEqual(d['Groceries'], ('outgoing', 45.2))
        self.assertEqual(d['Refund'], ('incoming', 12.0))

    def test_direction_column_absolute_amounts(self):
        text = (
            'Date,Description,Amount,Direction\n'
            '01/07/2026,Shop,12.50,Debit\n'
            '02/07/2026,Refund,3.00,Credit\n'
        )
        rows, meta = self._parse(text)
        # Explicit direction column → inference skipped; absolute + direction cell.
        self.assertIn(meta['amount_sign'], ('absolute', 'negative_is_outgoing'))
        d = self._dirs(rows)
        self.assertEqual(d['Shop'][0], 'outgoing')
        self.assertEqual(d['Refund'][0], 'incoming')

    def test_equal_pos_neg_falls_back_to_alias_default(self):
        text = (
            'Date,Description,Amount\n'
            '01/07/2026,A,10.00\n'
            '02/07/2026,B,-10.00\n'
        )
        rows, meta = self._parse(text)
        # Tie → alias default negative_is_outgoing.
        self.assertEqual(meta['amount_sign'], 'negative_is_outgoing')
        d = self._dirs(rows)
        self.assertEqual(d['A'][0], 'incoming')
        self.assertEqual(d['B'][0], 'outgoing')

    def test_infer_helper_majority_and_tie(self):
        col = {'date': 0, 'description': 1, 'amount': 2}
        pos_heavy = [
            ['01/07/2026', 'A', '10'],
            ['02/07/2026', 'B', '20'],
            ['03/07/2026', 'C', '-1'],
        ]
        neg_heavy = [
            ['01/07/2026', 'A', '-10'],
            ['02/07/2026', 'B', '-20'],
            ['03/07/2026', 'C', '1'],
        ]
        tie = [
            ['01/07/2026', 'A', '10'],
            ['02/07/2026', 'B', '-10'],
        ]
        self.assertEqual(
            app_mod._infer_amount_sign_from_data_rows(pos_heavy, col),
            'positive_is_outgoing',
        )
        self.assertEqual(
            app_mod._infer_amount_sign_from_data_rows(neg_heavy, col),
            'negative_is_outgoing',
        )
        self.assertIsNone(app_mod._infer_amount_sign_from_data_rows(tie, col))
        self.assertIsNone(
            app_mod._infer_amount_sign_from_data_rows(
                pos_heavy, {'date': 0, 'description': 1, 'amount': 2, 'direction': 3},
            )
        )

    def test_large_amex_month_stays_positive_outgoing(self):
        lines = ['Date,Description,Amount']
        for i in range(1, 41):
            day = f'{i:02d}' if i <= 31 else '31'
            lines.append(f'{day}/07/2026,MERCHANT {i},{10 + i * 0.35:.2f}')
        lines.append('15/07/2026,PAYMENT RECEIVED,-1200.00')
        lines.append('20/07/2026,STORE CREDIT,-25.00')
        text = '\n'.join(lines) + '\n'
        rows, meta = self._parse(text)
        self.assertEqual(meta['amount_sign'], 'positive_is_outgoing')
        out = [r for r in rows if r['direction'] == 'outgoing']
        inc = [r for r in rows if r['direction'] == 'incoming']
        self.assertEqual(len(out), 40)
        self.assertEqual(len(inc), 2)
        self.assertTrue(all(r['description'].startswith('MERCHANT') for r in out))


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
