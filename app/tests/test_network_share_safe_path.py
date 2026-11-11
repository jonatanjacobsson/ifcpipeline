"""``_safe_path`` must keep every network-share path inside the mount."""

import os
import sys
import tempfile
import unittest
from unittest import mock

# app/tests -> app -> ifcpipeline
_HERE = os.path.dirname(os.path.abspath(__file__))
_DASH = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "dashboard")
if _DASH not in sys.path:
    sys.path.insert(0, _DASH)

from services import network_share_service as ns  # noqa: E402


class SafePathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        self.share = os.path.join(root, "share")
        os.makedirs(os.path.join(self.share, "sub"))
        os.makedirs(os.path.join(root, "share-other"))
        os.symlink(os.path.join(root, "share-other"), os.path.join(self.share, "link"))
        patcher = mock.patch.object(ns.settings, "NETWORK_SHARE_PATH", self.share)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_inside_mount(self):
        real = os.path.realpath(self.share)
        self.assertEqual(ns._safe_path(""), real)
        self.assertEqual(ns._safe_path("sub"), os.path.join(real, "sub"))
        self.assertEqual(ns._safe_path("sub/../sub"), os.path.join(real, "sub"))

    def test_sibling_directory_with_shared_prefix_is_rejected(self):
        self.assertIsNone(ns._safe_path("../share-other"))
        self.assertIsNone(ns._safe_path("../share-other/secret.txt"))

    def test_traversal_and_symlink_escape_are_rejected(self):
        self.assertIsNone(ns._safe_path("../../etc/passwd"))
        self.assertIsNone(ns._safe_path("link/secret.txt"))


if __name__ == "__main__":
    unittest.main()
