import unittest
import tempfile
import json
import hashlib
from pathlib import Path
from threading import Event
from desktop_v4.backup_inventory import inventory,verify,safe_child

class BackupInventoryTests(unittest.TestCase):
    def test_manifest_verification_and_secret_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/"FULL_fixture";folder.mkdir()
            content=b"secret PPP password stays on disk"
            (folder/"server_FULL.json").write_bytes(content)
            p={"format":"LinkVideo.Helper VPN backup manifest","format_version":1,"created_at_utc":"2025-01-01",
               "servers_ok":["test"],"servers_failed":[],"files":[{"name":"server_FULL.json","size":len(content),"sha256":hashlib.sha256(content).hexdigest()}]}
            (folder/"manifest.json").write_text(json.dumps(p))
            self.assertEqual(inventory(root)[0]["id"],"FULL_fixture")
            result=verify(root,"FULL_fixture",Event(),lambda *_:None)
            self.assertTrue(result["verified"]);self.assertNotIn("password",str(result))
            (folder/"server_FULL.json").write_bytes(b"modified")
            self.assertFalse(verify(root,"FULL_fixture",Event(),lambda *_:None)["verified"])
            with self.assertRaises(ValueError):safe_child(root,"../test")
            p["files"][0]["name"]="../outside"
            (folder/"manifest.json").write_text(json.dumps(p))
            self.assertFalse(verify(root,"FULL_fixture",Event(),lambda *_:None)["verified"])
