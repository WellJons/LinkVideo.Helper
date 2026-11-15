import unittest
from pathlib import Path
from unittest.mock import patch
from desktop_v4 import background

def fixture(exe,args="--lv4-background-worker",user="S-1-5-18"):
    return f'<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Principals><Principal><UserId>{user}</UserId></Principal></Principals><Actions><Exec><Command>{exe}</Command><Arguments>{args}</Arguments></Exec></Actions></Task>'.encode()

class BackgroundTests(unittest.TestCase):
    def test_only_expected_task_action(self):
        exe=Path("C:/Program Files/LinkVideo.Helper.4.Background/helper-core.exe")
        self.assertTrue(background.expected_definition(fixture(exe),exe))
        self.assertFalse(background.expected_definition(fixture(exe,"--lv4-background-enable"),exe))
        self.assertFalse(background.expected_definition(fixture(exe,user="User"),exe))
        self.assertFalse(background.expected_definition(fixture("C:/Users/test/helper-core.exe"),exe))
        self.assertFalse(background.expected_definition(b"<invalid",exe))
    def test_no_elevated_entry_from_user_install(self):
        with patch.object(background,"is_runner",return_value=False):
            self.assertEqual(background.dispatch_cli(["--lv4-background-worker"]),3)
            self.assertEqual(background.dispatch_cli(["--lv4-background-enable","any"]),3)
            self.assertIsNone(background.dispatch_cli([]))
    def test_legacy_conflict_prevents_task_write(self):
        with patch.object(background,"task_names",return_value={background.LEGACY.casefold()}),patch.object(background,"command") as command:
            self.assertEqual(background.enable(Path("unused"),{}),18)
            command.assert_not_called()

    def test_hardlink_path_is_not_authorized(self):
        from types import SimpleNamespace as NS
        exe=Path("C:/Program Files/LinkVideo.Helper.4.Background/helper-core.exe")
        with patch.object(background,"executable",return_value=exe),patch.object(background,"sys",NS(frozen=True,executable="C:/Users/test/helper-core.exe")),patch.object(background,"installed",return_value=True) as installed:
            self.assertFalse(background.is_runner())
            installed.assert_not_called()
