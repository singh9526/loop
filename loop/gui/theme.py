"""The token table, and the Qt stylesheet built from it.

The same values the design previews use. System faces only: PySide6 has to
render them too, and bundling a face for parity with a web preview is a
cost with no return.

No Qt import here on purpose — this is a string builder, and it stays
testable on a machine with no PySide6.

Two kinds of token live in `TOKENS`. The first eleven of each mode are the
design's own `--ink` … `--crit`, copied verbatim, plus the meter ramp. The
rest are `color-mix()` results: Qt's stylesheet engine has no `color-mix`
and no alpha compositing over an unknown ground, so every mix the design
asks for is resolved to a literal here, against the ground it actually
sits on, and the recipe is written next to it. `docs`-style arithmetic
lives in the branch report; the recipe comment is what a reader needs to
re-derive a value if a base token ever moves.
"""

from __future__ import annotations

TOKENS: dict[str, dict[str, str]] = {
    "light": {
        "ink": "#F1F5F5", "panel": "#FFFFFF", "raised": "#E7EDED", "line": "#D0DBDB",
        "text": "#13201F", "muted": "#576C71", "dim": "#8598A0", "accent": "#0F857C",
        "ok": "#2C8A4E", "warn": "#A96F16", "crit": "#C04630",

        # The meter's own ramp. Not the accent/warn/crit trio: in light mode
        # those three are text colours and read muddy as a gradient, which
        # is why the design carries a second, saturated set for the track.
        "meter_cool": "#17998F", "meter_warm": "#C98A1E", "meter_hot": "#C04630",

        # --- color-mix() resolved to literals (mix over the stated ground) ---
        "active_tint": "#E5F2F1",     # accent 11% over panel   (.srow.active)
        "banner_bg": "#F7E7E4",       # crit   13% over panel   (.banner)
        "banner_line": "#CBA8A1",     # crit   34% over line    (.banner border)
        "error_bg": "#F5EEE3",        # warn   12% over panel   (.error)
        "error_line": "#C3B698",      # warn   34% over line    (.error border)
        "pill_live_line": "#8EBBA3",  # ok     40% over line    (.pill.live)
        "pill_dead_line": "#C99990",  # crit   44% over line    (.pill.dead)
        "hover_line": "#66ACA7",      # accent 55% over line    (.btn:hover)
        "danger_line": "#C99C93",     # crit   42% over line    (.btn.danger)
        "chip_ink": "#ADD3D1",        # ink    70% over accent  (.btn.primary .k)
        "chip_ink_line": "#53A7A0",   # ink    30% over accent  (.btn.primary .k border)
    },
    "dark": {
        "ink": "#0C1214", "panel": "#121B1E", "raised": "#19252A", "line": "#26363C",
        "text": "#DCE8EA", "muted": "#7B9198", "dim": "#506469", "accent": "#5AD1C8",
        "ok": "#5FCB7E", "warn": "#E4A33C", "crit": "#E4644E",

        "meter_cool": "#5AD1C8", "meter_warm": "#E4A33C", "meter_hot": "#E4644E",

        "active_tint": "#1A2F31",
        "banner_bg": "#2D2424",
        "banner_line": "#674642",
        "error_bg": "#2B2B22",
        "error_line": "#675B3C",
        "pill_live_line": "#3D7256",
        "pill_dead_line": "#7A4A44",
        "hover_line": "#438B89",
        "danger_line": "#764944",
        "chip_ink": "#234B4A",
        "chip_ink_line": "#439892",
    },
}

# The design's `--shadow`, kept here so the token table is the whole token
# table. Nothing in `gui/` renders it: Qt's stylesheet engine has no
# `box-shadow`, and every element that carries this token in the mockup
# (`.win`, `.dlg`, `.tray`, `.checkin`) is a real OS window in the app,
# already shadowed by the compositor. The *inset* box-shadows — which are
# rules, not shadows — are translated to `border-left` in `_QSS` below.
SHADOW: dict[str, str] = {
    "light": "0 1px 2px rgba(19,32,31,.08), 0 8px 24px rgba(19,32,31,.06)",
    "dark": "0 1px 2px rgba(0,0,0,.4), 0 12px 32px rgba(0,0,0,.34)",
}

# The design's stacks lead with `ui-monospace` / `-apple-system`, which are
# CSS generic keywords Qt's font-family parser does not know: it treats
# every entry as a literal family name and, when none match, falls through
# to the default application font rather than to a platform monospace.
# `SF Mono` and `SF Pro Text` are not registered family names on macOS
# either (SF Mono ships inside Terminal.app), so the previous stacks
# resolved *both* faces to the proportional `.AppleSystemUIFont` — a
# "monospace" clock with variable-width digits, and a 250ms
# `Populating font family aliases` penalty on every launch.
#
# `Menlo` is a real fixed-pitch family on every macOS, and
# `.AppleSystemUIFont` is what the system UI face actually resolves to.
# The Windows faces stay where they were: neither exists on macOS, so
# they cost nothing here and are the ones that resolve there.
MONO = '"Menlo", "Cascadia Mono", "JetBrains Mono", "Consolas", monospace'
UI = '".AppleSystemUIFont", "Segoe UI Variable Text", "Segoe UI", sans-serif'

_QSS = """
/* ---- ground --------------------------------------------------------
   `QWidget` sets the app-wide ground; the second rule takes it back off
   every label and frame, so a widget shows whatever it is sitting on
   instead of stamping an `ink` rectangle over it. Without it the
   dashboard's panel/raised/tinted grounds are invisible — every child
   repaints them away — which is most of why the built window read flat.
   Rules further down re-ground the containers that own a colour. */
QWidget { background: %(ink)s; color: %(text)s; font-family: %(ui)s; font-size: 13px; }
QLabel, QFrame { background: transparent; }

QWidget#page { background: %(panel)s; }
QFrame#panel { background: %(panel)s; border: 1px solid %(line)s; border-radius: 8px; }
QLabel#question { font-size: 18px; font-weight: 600; }
QLabel#label { font-family: %(mono)s; color: %(dim)s; font-size: 10px; letter-spacing: 1px; text-transform: uppercase; }
QLabel#muted { color: %(muted)s; }
QLabel#report_error { color: %(crit)s; }
QLabel#clock, QLabel#over { font-family: %(mono)s; font-size: 25px; }
QLabel#over { color: %(crit)s; }
QLabel#clock_of { font-family: %(mono)s; font-size: 15px; color: %(dim)s; }

/* ---- controls (.btn and its variants) -------------------------------
   Scoped to object names rather than bare `QPushButton` so the check-in
   overlay's buttons keep the sizing they were built with. */
QPushButton {
    background: %(raised)s; color: %(text)s; border: 1px solid %(line)s;
    border-radius: 6px; padding: 5px 12px;
}
QPushButton:hover { border-color: %(accent)s; }
QPushButton:disabled { color: %(dim)s; border-color: %(line)s; background: %(ink)s; }
QPushButton#btn, QPushButton#btn_primary, QPushButton#btn_ghost,
QPushButton#btn_danger, QPushButton#btn_sm {
    font-size: 13px; padding: 7px 12px; border-radius: 5px;
    border: 1px solid %(line)s; background: %(panel)s; color: %(text)s;
    /* Every button is sized to its own content, so left and centre are
       the same thing — except on the two carrying a `.k` chip, where a
       centred label would run underneath it. */
    text-align: left;
}
QPushButton#btn:hover, QPushButton#btn_primary:hover, QPushButton#btn_sm:hover {
    border-color: %(hover_line)s;
}
QPushButton#btn_primary {
    background: %(accent)s; border-color: %(accent)s; color: %(ink)s; font-weight: 600;
}
QPushButton#btn_ghost, QPushButton#btn_danger {
    background: transparent; border-color: transparent;
}
QPushButton#btn_ghost { color: %(muted)s; }
QPushButton#btn_ghost:hover { background: %(raised)s; border-color: %(line)s; color: %(text)s; }
QPushButton#btn_danger { color: %(crit)s; }
QPushButton#btn_danger:hover { background: %(raised)s; border-color: %(danger_line)s; }
QPushButton#btn_sm { font-size: 12px; padding: 4px 8px; }
/* The design draws no disabled state, and most of this toolbar is
   disabled whenever no loop is open — so disabled drops the fill rather
   than dimming it, or a greyed primary still reads as the live one. */
QPushButton#btn:disabled, QPushButton#btn_primary:disabled, QPushButton#btn_sm:disabled {
    color: %(dim)s; background: transparent; border-color: %(line)s;
}
QPushButton#btn_ghost:disabled, QPushButton#btn_danger:disabled {
    color: %(dim)s; background: transparent; border-color: transparent;
}
QLabel#k {
    font-family: %(mono)s; font-size: 10px; color: %(dim)s;
    border: 1px solid %(line)s; border-radius: 3px; padding: 1px 4px;
}
QLabel#k_on_accent {
    font-family: %(mono)s; font-size: 10px; color: %(chip_ink)s;
    border: 1px solid %(chip_ink_line)s; border-radius: 3px; padding: 1px 4px;
}

QLineEdit, QPlainTextEdit {
    background: %(ink)s; border: 1px solid %(line)s; border-radius: 6px;
    padding: 6px 8px; selection-background-color: %(accent)s;
}
QLineEdit:focus, QPlainTextEdit:focus { border-color: %(accent)s; }

/* ---- toolbar (.toolbar) --------------------------------------------- */
QFrame#toolbar { background: %(panel)s; border-bottom: 1px solid %(line)s; padding: 9px 18px; }

/* ---- instrument head (.head) ---------------------------------------- */
QFrame#head { padding: 16px 18px 18px 18px; }

/* .srow — the 2px accent bar is `box-shadow: inset 2px 0 0` in the
   design. Qt has no box-shadow, so it is a real border, and the left
   padding drops from 7px to 5px to keep the text where the mockup puts
   it (border + padding = the design's 7px). Paused rows carry the same
   border in `transparent` so both align. */
QFrame#srow, QFrame#srow_active {
    border-radius: 4px; padding: 4px 7px 4px 5px; border-left: 2px solid transparent;
}
QFrame#srow_active { background: %(active_tint)s; border-left: 2px solid %(accent)s; }
QLabel#srow_mark { font-family: %(mono)s; font-size: 11px; color: %(dim)s; }
QLabel#srow_mark_active { font-family: %(mono)s; font-size: 11px; color: %(accent)s; }
QLabel#srow_id { font-family: %(mono)s; font-size: 12px; color: %(dim)s; }
QLabel#srow_q { font-size: 13px; color: %(muted)s; }
QLabel#srow_q_active { font-size: 13px; font-weight: 600; color: %(text)s; }
QLabel#srow_meta { font-family: %(mono)s; font-size: 11px; color: %(dim)s; }
QLabel#srow_meta_stale { font-family: %(mono)s; font-size: 11px; color: %(warn)s; }

/* .stop */
QFrame#stop { padding: 0px 7px 0px 7px; }
QLabel#stop_value { font-size: 13px; color: %(muted)s; }

/* .meter */
QFrame#meter { padding: 0px 7px 0px 7px; }
QLabel#mright { font-family: %(mono)s; font-size: 12px; color: %(muted)s; }
QLabel#mright_value { font-family: %(mono)s; font-size: 12px; font-weight: 600; color: %(text)s; }
QLabel#scale { font-family: %(mono)s; font-size: 10px; color: %(dim)s; }

/* ---- thrash banner (.banner) ----------------------------------------
   A name of its own: `QFrame#banner` belongs to the check-in overlay,
   which reuses it for a different component. */
QFrame#thrash {
    background: %(banner_bg)s; border-top: 1px solid %(banner_line)s;
    border-bottom: 1px solid %(banner_line)s; border-left: 3px solid %(crit)s;
    padding: 12px 18px 12px 15px;
}
QLabel#thrash_glyph { font-family: %(mono)s; font-size: 15px; color: %(crit)s; }
QLabel#thrash_title { font-size: 14px; font-weight: 600; color: %(crit)s; }
QLabel#thrash_why { font-family: %(mono)s; font-size: 12px; color: %(muted)s; }
QLabel#thrash_fix { font-size: 13px; color: %(text)s; }

/* ---- error banner (.error) ------------------------------------------ */
QFrame#error {
    background: %(error_bg)s; border-bottom: 1px solid %(error_line)s;
    border-left: 3px solid %(warn)s; padding: 11px 18px 11px 15px;
}
QLabel#error_glyph { font-family: %(mono)s; font-size: 13px; color: %(warn)s; }
QLabel#error_text { font-size: 13px; color: %(text)s; }
QLabel#path { font-family: %(mono)s; color: %(muted)s; font-size: 12px; }

/* ---- two-column body (.cols) ---------------------------------------- */
QFrame#cols { border-top: 1px solid %(line)s; }
QFrame#col { padding: 15px 18px 15px 18px; }
QFrame#col_right { padding: 15px 18px 15px 18px; border-left: 1px solid %(line)s; }
QLabel#count { font-family: %(mono)s; font-size: 11px; color: %(dim)s; }

/* .hyp */
QFrame#hyp { border-radius: 4px; padding: 5px 6px 5px 6px; }
QLabel#hyp_mark { font-family: %(mono)s; font-size: 11px; color: %(accent)s; }
QLabel#hyp_mark_dead { font-family: %(mono)s; font-size: 11px; color: %(ok)s; }
QLabel#hyp_text { font-size: 13px; color: %(text)s; }
QLabel#hyp_text_dead { font-size: 13px; color: %(dim)s; }

/* .entry — `.because`'s rule is `box-shadow: inset 1px 0 0`, a hairline
   rather than a shadow; the 10px offset the design asks for is the 1px
   border plus 9px of padding. */
QLabel#entry_time { font-family: %(mono)s; font-size: 12px; color: %(dim)s; }
QLabel#entry_text { font-size: 13px; color: %(text)s; }
QLabel#entry_killed { font-size: 13px; color: %(ok)s; }
QLabel#entry_ping { font-size: 13px; color: %(muted)s; }
QLabel#because {
    font-size: 12px; color: %(muted)s;
    border-left: 1px solid %(line)s; padding-left: 9px;
}

/* ---- status strip (.strip) ------------------------------------------ */
QFrame#strip { background: %(raised)s; border-top: 1px solid %(line)s; padding: 8px 18px 8px 18px; }
QLabel#strip_text { font-family: %(mono)s; font-size: 11px; color: %(muted)s; }
QFrame#pill, QFrame#pill_live, QFrame#pill_dead {
    background: %(panel)s; border: 1px solid %(line)s; border-radius: 10px;
    padding: 2px 8px 2px 6px;
}
QFrame#pill_live { border-color: %(pill_live_line)s; }
QFrame#pill_dead { border-color: %(pill_dead_line)s; }
QLabel#pill_text { font-family: %(mono)s; font-size: 11px; color: %(muted)s; }
QLabel#pill_text_live { font-family: %(mono)s; font-size: 11px; color: %(ok)s; }
QLabel#pill_text_dead { font-family: %(mono)s; font-size: 11px; color: %(crit)s; }

/* ---- the report line, under the strip -------------------------------
   Not a design element; it is where `Actions.report` and the scheduler
   surface an outcome. Padded to the strip's gutter so it lines up. */
QFrame#report { padding: 8px 18px 8px 18px; }

/* The full-screen check-in. Read across a room, not across a desk: the
   sizes are the ones `tk.py` used, the tokens and faces are the
   dashboard's. Nothing below this line renders differently than it did
   before the dashboard was restyled — `#overlay_submit` merged its two
   rules into one and `#banner` moved down here from the dashboard block
   (the overlay is its only user), and neither changes a pixel.

   `#label` is deliberately not reused down here. It carries
   `text-transform: uppercase`, which Qt honours (it sets the font's
   capitalization), and every string in the overlay comes from
   `app/checkin.py` and has to render exactly as written. */
QFrame#banner { background: %(raised)s; border-left: 3px solid %(warn)s; border-radius: 4px; }
QLabel#overlay_title { color: %(dim)s; font-size: 18px; }
QLabel#overlay_question { font-size: 32px; font-weight: 600; }
QLabel#overlay_caption { color: %(dim)s; font-size: 18px; }
QLabel#overlay_warning { font-size: 18px; }
QLabel#overlay_missing { color: %(crit)s; font-size: 18px; }
QLabel#overlay_countdown { color: %(muted)s; font-family: %(mono)s; font-size: 16px; }
QPushButton#overlay_choice { font-size: 22px; padding: 8px 16px; }
/* The choice in force while its pick list or its fields are on screen.
   The row stays live — a choice is not committed until the answer is —
   so this is the only thing saying which question the fields belong to. */
/* Fill and border only. A heavier weight here would widen the label past
   the size hint Qt cached while the button was unchecked — a pseudo-state
   change does not re-ask for one — and clip it inside its own button. */
QPushButton#overlay_choice:checked {
    background: %(accent)s; border-color: %(accent)s; color: %(ink)s;
}
QPushButton#overlay_pick { font-size: 20px; padding: 8px 16px; }
QPushButton#overlay_submit {
    background: %(accent)s; color: %(panel)s; border-color: %(accent)s;
    font-size: 20px; padding: 8px 16px;
}
QLineEdit#overlay_field { font-family: %(mono)s; font-size: 20px; }
"""


def stylesheet(mode: str) -> str:
    """Raises KeyError on an unknown mode rather than rendering half a theme."""
    return _QSS % {**TOKENS[mode], "mono": MONO, "ui": UI}
