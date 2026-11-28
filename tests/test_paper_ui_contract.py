"""The paper UI keeps the fix19 session/price behavior and the rebuilt panel hooks."""
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static" / "paper.js").read_text()


class PaperUiContract(unittest.TestCase):
    def test_script_parses(self):
        r = subprocess.run(["node", "--check", str(ROOT / "static" / "paper.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_fix19_behaviors_stay(self):
        self.assertIn("window.xsMe", JS)
        self.assertIn("function ensurePx", JS)
        self.assertIn("inp.placeholder", JS)
        self.assertIn("#calCard,#dockCal,#bkCalBtn", JS)

    def test_one_panel_hooks(self):
        self.assertIn("function onTokenPage", JS)
        self.assertIn("function paintBadges", JS)
        self.assertIn("function paintBg", JS)
        self.assertIn("xScanner", JS)


if __name__ == "__main__":
    unittest.main()
