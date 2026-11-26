import json
import os
import tempfile
import unittest
from pathlib import Path
from desktop_v4.settings_store import ProfileSettings
from linkvideo_vpn_helper.services.server_registry import ServerRegistry


class SettingsTests(unittest.TestCase):
    def test_persistence_and_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "servers.json"
            s = ProfileSettings(path)
            r = ServerRegistry(s)
            r.add("test.example.invalid", "Казахстан")
            r.set_enabled("test.example.invalid", False)
            s.setValue("vpn_create/start_ip/test.example.invalid", "172.16.0.10")
            s.setValue("archive/extra_cluster_enabled/provzor", False)
            with self.assertRaises(ValueError):
                s.setValue("b2o/token", "secret")
            reopened = ProfileSettings(path)
            row = next(x for x in ServerRegistry(reopened).all() if x.host == "test.example.invalid")
            self.assertEqual(row.country, "Казахстан")
            self.assertFalse(row.enabled)
            self.assertFalse(reopened.value("archive/extra_cluster_enabled/provzor", True, bool))
            self.assertEqual(reopened.value("vpn_create/start_ip/test.example.invalid"), "172.16.0.10")
            reopened.remove("vpn_create")
            self.assertIsNone(ProfileSettings(path).value("vpn_create/start_ip/test.example.invalid"))

    def test_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "servers.ini"
            old.write_text('[vpn_servers]\ncustom_v2="[{\\\"host\\\": \\\"test.example.invalid\\\", \\\"country\\\": \\\"Казахстан\\\"}]"\ndisabled_v2="[\\\"test.example.invalid\\\"]"\n[archive]\nextra_cluster_enabled\\provzor=false\n[b2o]\ntoken=secret\n', encoding="utf-8")
            original = old.read_bytes()
            s = ProfileSettings(root / "servers.json", legacy=old)
            self.assertEqual(old.read_bytes(), original)
            self.assertNotIn("secret", (root / "servers.json").read_text(encoding="utf-8"))
            row = next(x for x in ServerRegistry(s).all() if x.host == "test.example.invalid")
            self.assertEqual(row.country, "Казахстан")
            self.assertFalse(row.enabled)
            self.assertFalse(s.value("archive/extra_cluster_enabled/provzor", True, bool))

    def test_bad_legacy_does_not_replace_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "servers.ini"
            old.write_text("[vpn_servers]\ncustom_v2=invalid\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                ProfileSettings(root / "servers.json", legacy=old)
            self.assertFalse((root / "servers.json").exists())
            self.assertTrue(old.exists())

    def test_real_qt_fixture(self):
        fixture = os.environ.get("LV4_QT_FIXTURE")
        if not fixture:
            self.skipTest("QSettings reference fixture is generated in Windows CI")
        with tempfile.TemporaryDirectory() as tmp:
            s = ProfileSettings(Path(tmp) / "servers.json", legacy=fixture)
            row = next(x for x in ServerRegistry(s).all() if x.host == "test.example.invalid")
            self.assertEqual(row.country, "Казахстан")
            self.assertFalse(row.enabled)
            self.assertFalse(s.value("archive/extra_cluster_enabled/provzor", True, bool))
            self.assertEqual(s.value("vpn_create/start_ip/test.example.invalid"), "172.16.0.10")
            self.assertEqual(json.loads(s.value("archive/custom_servers/241")), ["dvr.example.invalid", "another.example.invalid"])
