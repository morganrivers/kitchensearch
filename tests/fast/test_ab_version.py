import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
from urllib.parse import urlparse, parse_qs

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.argv[0] = str(_REPO_ROOT / "emoji-picker-tk.py")
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import picker_utils  # noqa: E402


def _decode_version(hex_str):
    payload = int(hex_str, 16) ^ picker_utils._AB_XOR_KEY
    return {
        "ab":          payload         & 0xF,
        "ts_bucket":  (payload >> 4)   & 0x3FFFF,
        "log_copies": (payload >> 22)  & 0xF,
        "settings":   (payload >> 26)  & 0x3FF,
    }


def _bit_for_key(key):
    return 1 << picker_utils._AB_SETTINGS_KEYS.index(key)


class AbVersionTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Path(tmp.name) / "config"
        cfg.mkdir()
        self._settings_path = cfg / "picker-settings.json"

        for p in (
            mock.patch.object(picker_utils, "CONFIG_DIR", cfg),
            mock.patch.dict(os.environ, {"KITCHENSEARCH_AB_MTIME_MS": "0"}),
        ):
            p.start()
            self.addCleanup(p.stop)

        self._fixed_time = picker_utils._AB_TS_EPOCH + 900
        tp = mock.patch("picker_utils.time.time", return_value=self._fixed_time)
        tp.start()
        self.addCleanup(tp.stop)

    def _write_settings(self, **kwargs):
        self._settings_path.write_text(json.dumps(kwargs), encoding="utf-8")

    def test_zero_settings_zero_copies(self):
        self._write_settings()
        fields = _decode_version(picker_utils._ab_version())
        self.assertEqual(fields["settings"],   0)
        self.assertEqual(fields["log_copies"], 0)
        self.assertEqual(fields["ts_bucket"],  1)
        self.assertEqual(fields["ab"],         0)

    def test_all_settings_bits_set(self):
        self._write_settings(**{k: True for k in picker_utils._AB_SETTINGS_KEYS})
        fields = _decode_version(picker_utils._ab_version())
        expected = (1 << len(picker_utils._AB_SETTINGS_KEYS)) - 1
        self.assertEqual(fields["settings"], expected)

    def test_each_settings_key_isolated(self):
        for key in picker_utils._AB_SETTINGS_KEYS:
            with self.subTest(key=key):
                self._write_settings(**{key: True})
                fields = _decode_version(picker_utils._ab_version())
                self.assertEqual(fields["settings"], _bit_for_key(key))

    def test_log_copies_is_floor_log2_clamped_15(self):
        cases = {0: 0, 1: 0, 2: 1, 3: 1, 4: 2, 100: 6, 2**14: 14, 2**15: 15, 2**20: 15}
        for cc, expected in cases.items():
            with self.subTest(copy_count=cc):
                self._write_settings(copy_count=cc)
                fields = _decode_version(picker_utils._ab_version())
                self.assertEqual(fields["log_copies"], expected)

    def test_negative_copy_count_treated_as_zero(self):
        self._write_settings(copy_count=-50)
        fields = _decode_version(picker_utils._ab_version())
        self.assertEqual(fields["log_copies"], 0)

    def test_ts_bucket_resolution_is_15_minutes(self):
        for offset_s, expected_bucket in [(0, 0), (899, 0), (900, 1), (3600, 4)]:
            with self.subTest(offset_s=offset_s):
                with mock.patch("picker_utils.time.time",
                                return_value=picker_utils._AB_TS_EPOCH + offset_s):
                    self._write_settings()
                    fields = _decode_version(picker_utils._ab_version())
                self.assertEqual(fields["ts_bucket"], expected_bucket)

    def test_ab_bucket_from_mtime_env_override(self):
        for ms in (0, 5, 6, 13, 15, 16, 31, 1234567890):
            with self.subTest(mtime_ms=ms):
                with mock.patch.dict(os.environ,
                                     {"KITCHENSEARCH_AB_MTIME_MS": str(ms)}):
                    self._write_settings()
                    fields = _decode_version(picker_utils._ab_version())
                self.assertEqual(fields["ab"], ms % 16)

    def test_url_format_and_version_param(self):
        self._write_settings()
        url = picker_utils.get_buymeacoffee_url()
        self.assertTrue(url.startswith(picker_utils._AB_BASE_URL))
        qs = parse_qs(urlparse(url).query)
        self.assertIn("version", qs)
        self.assertEqual(len(qs["version"][0]), 9)
        _decode_version(qs["version"][0])

    def test_corrupt_settings_json_treated_as_empty(self):
        self._settings_path.write_text("{not json", encoding="utf-8")
        fields = _decode_version(picker_utils._ab_version())
        self.assertEqual(fields["settings"],   0)
        self.assertEqual(fields["log_copies"], 0)

    def test_missing_settings_file_treated_as_empty(self):
        self.assertFalse(self._settings_path.exists())
        fields = _decode_version(picker_utils._ab_version())
        self.assertEqual(fields["settings"],   0)
        self.assertEqual(fields["log_copies"], 0)

    def test_payload_fits_in_36_bits(self):
        self._write_settings(
            copy_count=2**30,
            **{k: True for k in picker_utils._AB_SETTINGS_KEYS},
        )
        max_ts_offset = 0x7FFFF * 900
        with mock.patch("picker_utils.time.time",
                        return_value=picker_utils._AB_TS_EPOCH + max_ts_offset), \
             mock.patch.dict(os.environ, {"KITCHENSEARCH_AB_MTIME_MS": "5"}):
            v = picker_utils._ab_version()
        self.assertEqual(len(v), 9)
        decoded = int(v, 16) ^ picker_utils._AB_XOR_KEY
        self.assertLess(decoded, 1 << 36)


if __name__ == "__main__":
    unittest.main()
