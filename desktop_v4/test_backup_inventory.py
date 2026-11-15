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

class CloudPreflightTests(unittest.TestCase):
    def test_conflicts_never_return_raw_secrets(self):
        from desktop_v4.extra_operations import Extended
        from desktop_v4.operations import InputError
        from types import SimpleNamespace as NS
        x=Extended(NS())
        x.cloud=NS(request=lambda *a,**kw:{"ok":False,"conflicts":[{"type":"nat-port","value":10001,"owner":"SECRET_TOKEN"}]})
        with self.assertRaises(InputError) as err:x.cloud_preflight("host","client")
        self.assertIn("10001",str(err.exception));self.assertNotIn("SECRET_TOKEN",str(err.exception))
        x.cloud=NS(request=lambda *a,**kw:{"ok":True,"conflicts":[],"ports":["10002",10001]})
        self.assertEqual(x.cloud_preflight("host","client")["ports"],[10001,10002])
        x.cloud=NS(request=lambda *a,**kw:{"ok":True,"conflicts":[]})
        with self.assertRaises(InputError):x.cloud_preflight("host","client")
