"""Clipboard-copy transparency tests for ksapp.picker_utils.copy_image_to_clipboard.

Emoji-kitchen PNGs have a transparent background. These tests exercise the
single high-level entry point copy_image_to_clipboard once per OS branch and
assert that a transparency-preserving representation of the image reaches that
platform's clipboard sink:

  * Linux  - the raw source PNG bytes are handed to the xlib sink unchanged.
  * macOS  - osascript reads the source file as raw PNG (`PNGf`), unchanged.
  * Windows- alongside the (necessarily flat) CF_DIB, an RGBA PNG is placed on
             the clipboard under the registered "PNG" format.

The OS sinks are mocked so the tests are hermetic and run on any platform. What
is verified is the bytes/format the app *offers* the clipboard, not the OS
clipboard itself.

Known caveat (not a bug this test can fix): the Windows CF_DIB is built via
RGBA->RGB, so alpha=0 pixels flatten to black. Apps that paste only CF_DIB
(e.g. classic Paint) get a black background; apps that read the PNG format get
transparency. This test guards that the transparent PNG format stays present.
"""
import ctypes
import io
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ksapp import picker_utils  # noqa: E402


def _transparent_png(path: Path) -> None:
    """Write a 4x4 RGBA PNG whose top-left pixel is fully transparent."""
    img = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    img.putpixel((1, 1), (200, 30, 30, 255))  # one opaque pixel so it's not blank
    img.save(path, "PNG")


def _assert_rgba_transparent(testcase, data: bytes) -> None:
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    testcase.assertEqual(im.mode, "RGBA")
    testcase.assertEqual(im.getpixel((0, 0))[3], 0,
                         "top-left pixel lost its transparency")


class _CopyBase(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.png = Path(tmp.name) / "combo.png"
        _transparent_png(self.png)
        self.source_bytes = self.png.read_bytes()
        # Never fire a real desktop notification from these tests.
        p = mock.patch.object(picker_utils, "_notify")
        self.notify = p.start()
        self.addCleanup(p.stop)


class SourceImageHasTransparency(_CopyBase):
    def test_fixture_is_actually_transparent(self):
        _assert_rgba_transparent(self, self.source_bytes)


class FlattenOnWhiteFillsTransparentPixels(_CopyBase):
    def test_transparent_becomes_white_opaque_kept(self):
        img = Image.open(self.png).convert("RGBA")
        flat = picker_utils._flatten_on_white(img)
        self.assertEqual(flat.mode, "RGB")
        self.assertEqual(flat.getpixel((0, 0)), (255, 255, 255),
                         "transparent pixel should flatten to white, not black")
        self.assertEqual(flat.getpixel((1, 1)), (200, 30, 30),
                         "opaque pixel should be preserved")


class LinuxCopyPreservesTransparency(_CopyBase):
    def test_xlib_sink_receives_unchanged_rgba_png(self):
        with mock.patch.object(picker_utils.sys, "platform", "linux"), \
             mock.patch.dict(picker_utils.os.environ, {"DISPLAY": ":0"}), \
             mock.patch.object(picker_utils, "_copy_image_xlib") as xlib:
            picker_utils.copy_image_to_clipboard(str(self.png))

        xlib.assert_called_once()
        sent = xlib.call_args.args[0]
        self.assertEqual(sent, self.source_bytes,
                         "xlib should get the raw source PNG, byte-identical")
        _assert_rgba_transparent(self, sent)
        self.notify.assert_not_called()


class MacCopyPreservesTransparency(_CopyBase):
    def test_osascript_reads_raw_png_file(self):
        result = mock.Mock(returncode=0)
        with mock.patch.object(picker_utils.sys, "platform", "darwin"), \
             mock.patch.object(picker_utils.subprocess, "run",
                               return_value=result) as run:
            picker_utils.copy_image_to_clipboard(str(self.png))

        run.assert_called_once()
        cmd = run.call_args.args[0]
        script = cmd[-1]
        # Raw PNG read (PNGf) of our file -> alpha reaches the clipboard intact.
        self.assertIn("PNGf", script)
        self.assertIn(str(self.png), script)
        _assert_rgba_transparent(self, self.source_bytes)
        self.notify.assert_not_called()


class WindowsCopyOffersTransparentPngFormat(_CopyBase):
    def test_registered_png_format_carries_rgba(self):
        CF_DIB = 8
        CF_PNG = 0xC0DE  # sentinel returned by RegisterClipboardFormatW

        windll = mock.MagicMock()
        windll.user32.RegisterClipboardFormatW.return_value = CF_PNG

        memmoved = []

        def _capture_memmove(dst, data, size):
            memmoved.append(bytes(data))

        with mock.patch.object(picker_utils.sys, "platform", "win32"), \
             mock.patch.object(ctypes, "windll", windll, create=True), \
             mock.patch.object(ctypes, "memmove", side_effect=_capture_memmove):
            picker_utils.copy_image_to_clipboard(str(self.png))

        self.notify.assert_not_called()

        set_calls = windll.user32.SetClipboardData.call_args_list
        formats = [c.args[0] for c in set_calls]
        self.assertIn(CF_DIB, formats, "CF_DIB not set")
        self.assertIn(CF_PNG, formats, "transparent PNG format not set")

        # Exactly one of the buffers copied to the clipboard must be a PNG that
        # still decodes to RGBA with a transparent pixel.
        pngs = [b for b in memmoved if b[:8] == b"\x89PNG\r\n\x1a\n"]
        self.assertTrue(pngs, "no PNG buffer placed on the Windows clipboard")
        _assert_rgba_transparent(self, pngs[0])


if __name__ == "__main__":
    unittest.main()
