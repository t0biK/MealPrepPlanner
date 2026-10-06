import json
import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def load(lang):
    return json.loads((STATIC / "i18n" / f"{lang}.json").read_text(encoding="utf-8"))


class I18nTest(unittest.TestCase):
    def test_same_keys(self):
        self.assertEqual(load("de").keys(), load("en").keys())

    def test_keys_used_in_app_js_exist(self):
        src = (STATIC / "app.js").read_text(encoding="utf-8")
        used = set(re.findall(r"\bt\(\s*[\"']([\w.]+)[\"']", src))
        self.assertTrue(used)
        self.assertEqual(used - load("de").keys(), set())


if __name__ == "__main__":
    unittest.main()
