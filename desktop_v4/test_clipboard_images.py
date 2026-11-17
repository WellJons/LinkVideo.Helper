import base64
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from desktop_v4.extra_operations import Extended

class Store:
    def __init__(self, rows):self.rows=rows
    def get(self,tid):return SimpleNamespace(body="Проверка")
    def images(self,tid):return self.rows

class TemplatesClipboardTests(unittest.TestCase):
    def test_export_does_not_require_qtgui(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);raw=b"\x89PNG\r\n\x1a\n"+"test".encode()
            image=folder/"sample.png";image.write_bytes(raw)
            bad=folder/"wrong.jpg";bad.write_bytes(b"not an image")
            obj=Extended(SimpleNamespace(template_store=lambda:Store([(1,image),(2,bad)])))
            result=obj.dispatch("templates_reply",{"id":1,"image":None},Event(),lambda *_:None)
            self.assertEqual(result["text"],"Проверка")
            self.assertEqual(result["skipped"],1)
            self.assertEqual(base64.b64decode(result["images"][0].split(",",1)[1]),raw)
            selected=obj.dispatch("templates_reply",{"id":1,"image":1},Event(),lambda *_:None)
            self.assertEqual(selected["text"],"")
