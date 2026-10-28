import tempfile
import unittest
from pathlib import Path

from app import config


class TestReadJson(unittest.TestCase):
    def test_reads_plain_utf8(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text('{"a": 1}', encoding="utf-8")
            self.assertEqual(config._read_json(path), {"a": 1})

    def test_reads_utf8_with_bom(self):
        # Confirmed real: Windows PowerShell's `Set-Content -Encoding
        # utf8` (and `Out-File`) write a UTF-8 BOM by default. Plain
        # utf-8 doesn't strip it, so json.load chokes on the leading
        # character with "Expecting value: line 1 column 1 (char 0)" --
        # indistinguishable at a glance from a genuinely empty file.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_bytes(b"\xef\xbb\xbf" + b'{"a": 1}')
            self.assertEqual(config._read_json(path), {"a": 1})

    def test_missing_file_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "does_not_exist.json"
            self.assertEqual(config._read_json(path), {})


if __name__ == "__main__":
    unittest.main()
