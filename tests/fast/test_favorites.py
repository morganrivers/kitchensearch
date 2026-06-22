"""Storage-layer tests for the favorites list in ksapp.picker_utils.

Favorites live in a JSON file under CONFIG_DIR. These tests redirect that path
to a TemporaryDirectory so they can run hermetically without touching the
user's real config.
"""
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ksapp import picker_utils  # noqa: E402


class _FavoritesBase(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cfg = Path(tmp.name)
        # Patch both CONFIG_DIR and the FAVORITES_PATH that was bound at
        # import time, so writes land in our tmpdir.
        self._patches = [
            mock.patch.object(picker_utils, "CONFIG_DIR", self.cfg),
            mock.patch.object(picker_utils, "FAVORITES_PATH",
                              self.cfg / "favorites.json"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)


class LoadFavoritesHandlesMissing(_FavoritesBase):
    def test_returns_empty_list_when_file_absent(self):
        self.assertEqual(picker_utils.load_favorites(), [])

    def test_returns_empty_list_when_file_is_garbage(self):
        (self.cfg / "favorites.json").write_text("not json")
        self.assertEqual(picker_utils.load_favorites(), [])

    def test_returns_empty_list_when_top_level_is_not_list(self):
        (self.cfg / "favorites.json").write_text('{"items": []}')
        self.assertEqual(picker_utils.load_favorites(), [])


class AddFavorite(_FavoritesBase):
    def test_add_inserts_newest_first(self):
        picker_utils.add_favorite("a", "u1", "ta")
        picker_utils.add_favorite("b", "u2", "tb")
        favs = picker_utils.load_favorites()
        self.assertEqual([f["url"] for f in favs], ["u2", "u1"])

    def test_add_dedupes_by_url(self):
        picker_utils.add_favorite("first-name", "u1", "ta")
        picker_utils.add_favorite("second-name", "u1", "tb")
        favs = picker_utils.load_favorites()
        self.assertEqual(len(favs), 1)
        # The second add replaces the first, keeping the newer alt+text.
        self.assertEqual(favs[0]["alt"],  "second-name")
        self.assertEqual(favs[0]["text"], "tb")

    def test_add_persists_to_disk(self):
        picker_utils.add_favorite("a", "u1", "ta")
        on_disk = json.loads((self.cfg / "favorites.json").read_text())
        self.assertEqual(on_disk[0]["url"], "u1")


class RemoveFavorite(_FavoritesBase):
    def test_remove_present_url(self):
        picker_utils.add_favorite("a", "u1", "")
        picker_utils.add_favorite("b", "u2", "")
        picker_utils.remove_favorite("u1")
        self.assertEqual([f["url"] for f in picker_utils.load_favorites()],
                         ["u2"])

    def test_remove_missing_url_is_noop(self):
        picker_utils.add_favorite("a", "u1", "")
        picker_utils.remove_favorite("u-other")
        self.assertEqual([f["url"] for f in picker_utils.load_favorites()],
                         ["u1"])


class ToggleFavorite(_FavoritesBase):
    def test_toggle_adds_when_absent_and_returns_true(self):
        added = picker_utils.toggle_favorite("a", "u1", "")
        self.assertTrue(added)
        self.assertEqual(len(picker_utils.load_favorites()), 1)

    def test_toggle_removes_when_present_and_returns_false(self):
        picker_utils.add_favorite("a", "u1", "")
        removed = picker_utils.toggle_favorite("a", "u1", "")
        self.assertFalse(removed)
        self.assertEqual(picker_utils.load_favorites(), [])


class IsFavorite(_FavoritesBase):
    def test_is_favorite_true_after_add(self):
        picker_utils.add_favorite("a", "u1", "")
        self.assertTrue(picker_utils.is_favorite("u1"))

    def test_is_favorite_false_when_absent(self):
        self.assertFalse(picker_utils.is_favorite("u-nope"))


if __name__ == "__main__":
    unittest.main()
