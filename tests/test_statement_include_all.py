"""UI presence checks for statement import header select-all checkboxes."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestStatementIncludeAllUi(unittest.TestCase):
    def test_preview_js_has_select_all_helpers(self):
        js = (ROOT / 'static' / 'spending.js').read_text(encoding='utf-8')
        self.assertIn('preview-include-all', js)
        self.assertIn('syncPreviewIncludeAll', js)
        self.assertIn("querySelectorAll('.preview-include')", js)
        self.assertIn('togglePreviewRow', js)

    def test_loan_statement_import_has_select_all_checkbox(self):
        index = (ROOT / 'templates' / 'index.html').read_text(encoding='utf-8')
        js = (ROOT / 'static' / 'script.js').read_text(encoding='utf-8')
        css = (ROOT / 'static' / 'style.css').read_text(encoding='utf-8')
        self.assertIn('id="statement-include-all"', index)
        self.assertIn('Select or deselect all rows', index)
        self.assertIn('statement-include-all', js)
        self.assertIn('syncStatementIncludeAll', js)
        self.assertIn("querySelectorAll('.statement-row-check')", js)
        self.assertIn('loan-import-row', js)
        self.assertIn('loan-import-row--open', css)
        self.assertIn('Tap a row to edit your share', index)


if __name__ == '__main__':
    unittest.main()
