import tempfile
import unittest
from pathlib import Path
from threading import Event
from linkvideo_vpn_helper.services.support_templates import TemplateStore
from desktop_v4.template_migration import migrate_templates

class TemplateMigrationTests(unittest.TestCase):
    def test_merge_and_preserve_attachments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            old=TemplateStore(root/"old");new=TemplateStore(root/"new")
            ident=old.save("Title","Text","Category")
            old.favorite(ident,True)
            pic=root/"picture.png";pic.write_bytes(b"test image content")
            old.attach_image(ident,pic)
            new.save("Existing","Keep","Other")
            result=migrate_templates(old.path,new)
            self.assertEqual(result,{"imported":1,"skipped":0,"images":1,"missing":0})
            rows=new.list()
            row=next(x for x in rows if x.title=="Title")
            self.assertTrue(row.favorite)
            self.assertEqual(new.images(row.id)[0][1].read_bytes(),pic.read_bytes())
            self.assertEqual(migrate_templates(old.path,new)["skipped"],1)
            self.assertEqual(len(new.list()),2)
            self.assertEqual(len(old.list()),1)
    def test_cancelled_migration_keeps_existing_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);old=TemplateStore(root/"old");new=TemplateStore(root/"new")
            old.save("New","Text","Cat");new.save("Existing","Text","Cat")
            event=Event();event.set()
            with self.assertRaises(ValueError): migrate_templates(old.path,new,event)
            self.assertEqual([x.title for x in new.list()],["Existing"])
