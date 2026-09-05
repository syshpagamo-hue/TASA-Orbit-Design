from __future__ import annotations

import builtins
import sys
import types

from tasa_v4 import cli

from conftest import ROOT


def test_gui_command_falls_back_when_tkinter_is_missing(monkeypatch):
    called: list[str] = []
    fake_web = types.ModuleType("tasa_v4.competition_web")
    fake_web.launch_web_ui = lambda path: called.append(str(path))
    monkeypatch.setitem(sys.modules, "tasa_v4.competition_web", fake_web)

    original_import = builtins.__import__

    def controlled_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "competition_gui" and level == 1:
            raise ModuleNotFoundError("No module named '_tkinter'", name="_tkinter")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", controlled_import)
    config = ROOT / "configs" / "current_competition.yaml"
    assert cli.main(["gui", str(config)]) == 0
    assert called == [str(config)]

