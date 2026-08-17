"""The token table, and the Qt stylesheet built from it.

The same values the design previews use. System faces only: PySide6 has to
render them too, and bundling a face for parity with a web preview is a
cost with no return.

No Qt import here on purpose — this is a string builder, and it stays
testable on a machine with no PySide6.
"""

from __future__ import annotations

TOKENS: dict[str, dict[str, str]] = {
    "light": {
        "ink": "#F1F5F5", "panel": "#FFFFFF", "raised": "#E7EDED", "line": "#D0DBDB",
        "text": "#13201F", "muted": "#576C71", "dim": "#8598A0", "accent": "#0F857C",
        "ok": "#2C8A4E", "warn": "#A96F16", "crit": "#C04630",
    },
    "dark": {
        "ink": "#0C1214", "panel": "#121B1E", "raised": "#19252A", "line": "#26363C",
        "text": "#DCE8EA", "muted": "#7B9198", "dim": "#506469", "accent": "#5AD1C8",
        "ok": "#5FCB7E", "warn": "#E4A33C", "crit": "#E4644E",
    },
}

MONO = '"SF Mono", "Cascadia Mono", "JetBrains Mono", Consolas, monospace'
UI = '"SF Pro Text", "Segoe UI Variable Text", "Segoe UI", sans-serif'

_QSS = """
QWidget { background: %(ink)s; color: %(text)s; font-family: %(ui)s; font-size: 13px; }
QFrame#panel { background: %(panel)s; border: 1px solid %(line)s; border-radius: 8px; }
QLabel#question { font-size: 18px; font-weight: 600; }
QLabel#label { color: %(dim)s; font-size: 10px; letter-spacing: 1px; text-transform: uppercase; }
QLabel#muted { color: %(muted)s; }
QLabel#clock, QLabel#over { font-family: %(mono)s; font-size: 26px; font-weight: 600; }
QLabel#over { color: %(crit)s; }
QPushButton {
    background: %(raised)s; color: %(text)s; border: 1px solid %(line)s;
    border-radius: 6px; padding: 5px 12px;
}
QPushButton:hover { border-color: %(accent)s; }
QPushButton:disabled { color: %(dim)s; border-color: %(line)s; background: %(ink)s; }
QPushButton#primary, QPushButton#overlay_submit {
    background: %(accent)s; color: %(panel)s; border-color: %(accent)s;
}
QPushButton#danger { color: %(crit)s; }
QLineEdit, QPlainTextEdit {
    background: %(ink)s; border: 1px solid %(line)s; border-radius: 6px;
    padding: 6px 8px; selection-background-color: %(accent)s;
}
QLineEdit:focus, QPlainTextEdit:focus { border-color: %(accent)s; }
QFrame#banner { background: %(raised)s; border-left: 3px solid %(warn)s; border-radius: 4px; }
QFrame#error { background: %(raised)s; border-left: 3px solid %(crit)s; border-radius: 4px; }
QLabel#dead { color: %(dim)s; text-decoration: line-through; }
QLabel#path { font-family: %(mono)s; color: %(muted)s; font-size: 11px; }

/* The full-screen check-in. Read across a room, not across a desk: the
   sizes are the ones `tk.py` used, the tokens and faces are the
   dashboard's. Nothing above this line changes.

   `#label` is deliberately not reused down here. It carries
   `text-transform: uppercase`, which Qt honours (it sets the font's
   capitalization), and every string in the overlay comes from
   `app/checkin.py` and has to render exactly as written. */
QLabel#overlay_title { color: %(dim)s; font-size: 18px; }
QLabel#overlay_question { font-size: 32px; font-weight: 600; }
QLabel#overlay_caption { color: %(dim)s; font-size: 18px; }
QLabel#overlay_warning { font-size: 18px; }
QLabel#overlay_missing { color: %(crit)s; font-size: 18px; }
QLabel#overlay_countdown { color: %(muted)s; font-family: %(mono)s; font-size: 16px; }
QPushButton#overlay_choice { font-size: 22px; padding: 8px 16px; }
QPushButton#overlay_pick { font-size: 20px; padding: 8px 16px; }
QPushButton#overlay_submit { font-size: 20px; padding: 8px 16px; }
QLineEdit#overlay_field { font-family: %(mono)s; font-size: 20px; }
"""


def stylesheet(mode: str) -> str:
    """Raises KeyError on an unknown mode rather than rendering half a theme."""
    return _QSS % {**TOKENS[mode], "mono": MONO, "ui": UI}
