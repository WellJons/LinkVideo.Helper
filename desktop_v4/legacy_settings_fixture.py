"""CI only: generate actual Qt 6 INI fixture before removing Qt from build environment."""
import json
import sys
from PySide6.QtCore import QSettings

s = QSettings(sys.argv[1], QSettings.Format.IniFormat)
s.setValue("vpn_servers/custom_v2", json.dumps([{"host": "test.example.invalid", "country": "Казахстан"}], ensure_ascii=False))
s.setValue("vpn_servers/disabled_v2", json.dumps(["test.example.invalid"]))
s.setValue("archive/extra_cluster_enabled/provzor", False)
s.setValue("vpn_create/start_ip/test.example.invalid", "172.16.0.10")
s.setValue("archive/custom_servers/241", json.dumps(["dvr.example.invalid", "another.example.invalid"]))
s.sync()
if s.status() != QSettings.Status.NoError:
    raise RuntimeError("Could not write reference fixture")
