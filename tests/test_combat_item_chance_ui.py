"""Smoke-test the source GUI's independent staged activation settings."""
from pathlib import Path
import runpy
import sys
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'win32', 'Native Win32 edit controls require Windows')
class CombatItemChanceUiTests(unittest.TestCase):
    def test_selection_original_toggle_staging_and_cancel(self):
        root = Path(__file__).resolve().parents[1]
        ns = runpy.run_path(str(root / 'CDSExecutablePatcher.pyw'), run_name='patcher_ui_test')
        cls = ns['CDSExecutablePatcher']
        with patch.object(cls, '_show_splash', lambda self: None):
            app = cls()
        try:
            fixture = root / 'tests' / 'fixtures' / 'coordinate_compass_test.exe'
            before = fixture.read_bytes()
            app._load_item_records(ns['read_item_records'](fixture), {})
            for item in range(4):
                app.item_list.selection_set(str(item))
                app._on_item_selected()
                self.assertEqual('disabled' in app.item_chance_button.state(), item == 3)

            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)

            def open_dialog(item):
                app.item_list.selection_set(str(item))
                app._on_item_selected()
                previous = set(app.winfo_children())
                app._show_combat_item_chance_editor()
                app.update_idletasks()
                window = next(w for w in app.winfo_children()
                              if isinstance(w, tk.Toplevel) and w not in previous)
                children = list(descendants(window))
                check = next(w for w in children if isinstance(w, ttk.Checkbutton))
                spin = next(w for w in children if isinstance(w, ttk.Spinbox))
                buttons = {w.cget('text'): w for w in children if isinstance(w, ttk.Button)}
                return window, check, spin, buttons

            for item in range(3):
                window, check, spin, buttons = open_dialog(item)
                self.assertIn('disabled', spin.state())
                check.invoke()
                self.assertNotIn('disabled', spin.state())
                spin.set('100')
                buttons['확인'].invoke()
                self.assertFalse(window.winfo_exists())
            self.assertEqual(app._combat_item_chances, ns['CombatItemChances'](100, 100, 100))
            window, check, spin, buttons = open_dialog(0)
            self.assertEqual(spin.get(), '100')
            spin.set('0')
            buttons['취소'].invoke()
            self.assertEqual(app._combat_item_chances.submarine_bomb, 100)
            window, check, spin, buttons = open_dialog(0)
            check.invoke()
            self.assertIn('disabled', spin.state())
            buttons['확인'].invoke()
            self.assertEqual(app._combat_item_chances, ns['CombatItemChances'](None, 100, 100))
            # Editing dialogs must not write the selected executable themselves.
            self.assertEqual(fixture.read_bytes(), before)
        finally:
            app.destroy()


if __name__ == '__main__':
    unittest.main()
