"""The logbook: `stats` and `grep` as a second view in the window.

Read-only — there is no `Actions` here and nothing is ever written, so
none of `actions.py`'s error-handling machinery (`StaleError`,
`LockTimeout`) applies. The widget binds `stats.compute`'s report and
`search.find`'s matches; formatting a number is `stats.format_drift`'s
job and a duration is `timefmt.format_duration`'s, same as everywhere
else in `gui/` — this module lays out labels, it does not derive them.

`controller` is accepted (mirroring every other top-level view in this
window) but is not required to read: like `cli.py`'s own `stats`/`grep`
commands, this widget reads the event log directly through
`loop.store.jsonl` on demand — when the user opens the logbook or
submits a search — rather than caching the controller's poll. Logbook
data does not need to track the clock at 1Hz the way the dashboard's
burn meter does; a query answered on request is enough for a read that
already means "let me check something."

Reading the log directly means this widget, not just `Controller`, can
meet `jsonl.CorruptLogError` — the recoverable-interior-corruption case
the whole app is built to tolerate. `_on_search` (the one read path;
`refresh` calls it too) catches it and reuses `Controller`'s own
fallback — `view.unreadable` bound into a `widgets.ErrorBanner` — rather
than inventing a second "the log is broken" message.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Callable

from PySide6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget,
)

from loop.app import view
from loop.core import events, search, stats
from loop.core.timefmt import format_duration
from loop.gui.widgets import ErrorBanner
from loop.store import jsonl, paths

# `ErrorBanner.bind` only ever reads `.error` off whatever it's given —
# a real `DashboardView` when the dashboard uses it, this stand-in here.
# Building a full `DashboardView` just to say "no error" would need every
# other one of its fields for no reason.
_NO_ERROR = SimpleNamespace(error=None)

# (label, value-getter) for the six substantive lines `stats.render` prints
# — its own two blank lines are terminal spacing, not data, and are left
# out here. The label strings are the same ones `stats.render` uses, so a
# reader flipping between the window and `loop stats` sees the same words.
ROWS: tuple[tuple[str, Callable[[dict], str]], ...] = (
    ("protocol followed", lambda r: _bucket_text(r["followed"])),
    ("protocol abandoned", lambda r: _bucket_text(r["abandoned"])),
    ("thrash episodes", lambda r: str(r["thrash_episodes"]["total"])),
    ("estimate drift", lambda r: _drift_text(r["estimate_drift"])),
    ("ping response", lambda r: _ping_text(r["ping_response"])),
    ("interruptions", lambda r: _interruptions_text(r["interruptions"])),
)


def _bucket_text(bucket: dict) -> str:
    return (f"{bucket['count']} loops · median {format_duration(bucket['median_s'])} "
            f"· {bucket['resolved_pct']}% resolved")


def _drift_text(drift: dict) -> str:
    # `stats.format_drift` owns the sign -> word decision (Task 9's fix)
    # so the window and the terminal can never disagree about it.
    return (f"median {stats.format_drift(drift['median_pct'])} budget · "
            f"{drift['extensions']} extensions across {drift['loops_with_extensions']} loops")


def _ping_text(ping: dict) -> str:
    return f"{ping['answered']} answered, {ping['timed_out']} timed out"


def _interruptions_text(interruptions: dict) -> str:
    return (f"median {interruptions['median_pauses']} pauses/loop · "
            f"median pause {format_duration(interruptions['median_pause_s'])} "
            f"· max depth {interruptions['max_depth']}")


class LogbookView(QWidget):
    def __init__(self, controller) -> None:
        super().__init__()
        self._controller = controller
        layout = QVBoxLayout(self)

        self._error = ErrorBanner()
        self._error.setVisible(False)  # hidden until the first read, good or bad
        layout.addWidget(self._error)

        self._search = QLineEdit()
        self._search.setPlaceholderText("search closed and abandoned loops…")
        self._search.returnPressed.connect(self._on_search)
        layout.addWidget(self._search)

        stats_panel = QFrame()
        stats_panel.setObjectName("panel")
        grid = QGridLayout(stats_panel)
        self._value_labels: dict[str, QLabel] = {}
        for row, (name, _getter) in enumerate(ROWS):
            caption = QLabel(name)
            caption.setObjectName("label")
            value = QLabel()
            grid.addWidget(caption, row, 0)
            grid.addWidget(value, row, 1)
            self._value_labels[name] = value
        layout.addWidget(stats_panel)

        matches_body = QWidget()
        self._matches_layout = QVBoxLayout(matches_body)
        self._matches_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(matches_body)
        layout.addWidget(scroll, 1)

    def refresh(self) -> None:
        """Re-read the log and re-run whatever term is currently in the
        search box. The window calls this when the logbook becomes the
        visible page; `_on_search` calls it on Enter."""
        self._on_search()

    def _on_search(self) -> None:
        term = self._search.text().strip()
        try:
            log = jsonl.read_all()
        except jsonl.CorruptLogError as exc:
            self._show_unreadable(exc)
            return
        self._error.bind(_NO_ERROR)
        report = stats.compute(log, time.time())
        matches = search.find(events.fold(log), log, term) if term else []
        self.bind(report, matches)

    def _show_unreadable(self, exc: jsonl.CorruptLogError) -> None:
        """The same degraded view `Controller.refresh` already shows for
        this exact exception, reused rather than reinvented: the stats
        grid and any matches already on screen are left exactly as they
        were — the last state that folded cleanly beats a blank or
        half-updated one, and re-deriving anything from a log we cannot
        fold would only be guessing."""
        self._error.bind(view.unreadable(
            None, path=str(paths.events_path()), line=_line_number(exc), frozen_at=0.0,
        ))

    def bind(self, report: dict, matches: list[search.Match]) -> None:
        for name, getter in ROWS:
            self._value_labels[name].setText(getter(report))

        _clear(self._matches_layout)
        for match in matches:
            header = QLabel(f"#{match.loop_id}  {match.question}")
            header.setObjectName("question")
            self._matches_layout.addWidget(header)
            for hit in match.hits:
                hit_label = QLabel(f"{hit.label}: {hit.text}")
                hit_label.setWordWrap(True)
                self._matches_layout.addWidget(hit_label)
        self._matches_layout.addStretch(1)


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()


def _line_number(exc: jsonl.CorruptLogError) -> int:
    """Same parse `Controller` does: `CorruptLogError` carries the line in
    its message ('<path>: line N is not valid JSON'), and there is no
    structured field to read it from instead. Duplicated rather than
    imported because `Controller`'s copy is a private, undocumented
    detail of a class this widget otherwise has no reason to depend on —
    matching its *behaviour* here, not adding a coupling to it."""
    text = str(exc)
    marker = "line "
    if marker not in text:
        return 0
    tail = text.split(marker, 1)[1]
    digits = tail.split(" ", 1)[0]
    return int(digits) if digits.isdigit() else 0
