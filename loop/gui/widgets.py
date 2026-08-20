"""The panels. Each takes a view model and draws it. None of them decide anything.

Every size, gap and padding here is the design's. Qt's default 9px layout
margins and 6px spacings are wrong everywhere, so every layout in this
module sets both explicitly — a layout that inherits the defaults is a
bug, not a shortcut.

Four of the design's rules have no Qt stylesheet equivalent and are drawn
in code instead of being written and silently ignored: the meter's mask
and ramp (`BurnMeter`), the strikethrough colour on a ruled-out
hypothesis (`StruckLabel`), the LED's glow ring (`Led`), and the
hover-reveal on row controls (`HoverRow`/`RowActions`).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from loop.app.view import DashboardView, MeterView
from loop.core import timeline
from loop.core.timefmt import format_clock, format_duration, format_mmss
from loop.gui import theme

TRACK_H = 9
CHECKPOINT_FRACTION = 0.75
SCALE_H = 13

# .srow's grid: 15px | 34px | 1fr | auto | auto, gap 9.
MARK_W = 15
ID_W = 34
ROW_GAP = 9
# .entry's grid: 46px | 1fr, gap 10.
TIME_W = 46
ENTRY_GAP = 10

CHIP_GAP = 6
BTN_PAD_X = 12  # .btn { padding: 7px 12px }

THRASH_GLYPH = "▲"
ERROR_GLYPH = "⚠"

# `.entry.killed` / `.entry.ping` — the only two variants the design draws.
ENTRY_STYLES = {timeline.RULED_OUT: "entry_killed", timeline.PING: "entry_ping"}


def _alpha(colour: str, fraction: float) -> QColor:
    """`color-mix(in srgb, <colour> n%, transparent)`, which is just that
    colour at n% alpha. Painted rather than resolved to a literal because
    the ground underneath these two is the meter's own gradient, which no
    token can name."""
    value = QColor(colour)
    value.setAlphaF(fraction)
    return value


class ElidedLabel(QLabel):
    """`.srow .q`'s `text-overflow: ellipsis`, which Qt has no property for.

    `text()` still returns the whole string — the ellipsis is a painting
    decision, so nothing that reads the label sees a truncated question.
    """

    def __init__(self, text: str = "") -> None:
        super().__init__(text)
        # Without Ignored the label's own sizeHint sets a floor and a long
        # question widens the window instead of eliding.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        rect = self.contentsRect()
        metrics = self.fontMetrics()
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(
            rect,
            int(self.alignment()) | int(Qt.TextSingleLine),
            metrics.elidedText(self.text(), Qt.ElideRight, rect.width()),
        )
        painter.end()


class StruckLabel(ElidedLabel):
    """`.hyp.dead .t`: struck through in the `ok` colour, not the text's.

    Qt honours `text-decoration: line-through` but has no
    `text-decoration-color`, and draws the line in the text colour — which
    here is `dim`. The design strikes in `ok` at 65% precisely so the line
    reads as "answered", so the rule is drawn rather than declared.
    """

    def __init__(self, text: str, mode: str) -> None:
        super().__init__(text)
        self._mode = mode

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        rect = self.contentsRect()
        metrics = self.fontMetrics()
        drawn = min(
            metrics.horizontalAdvance(
                metrics.elidedText(self.text(), Qt.ElideRight, rect.width())
            ),
            rect.width(),
        )
        baseline = rect.top() + (rect.height() - metrics.height()) / 2 + metrics.ascent()
        y = int(baseline - metrics.strikeOutPos())
        painter = QPainter(self)
        painter.setPen(_alpha(theme.TOKENS[self._mode]["ok"], 0.65))
        painter.drawLine(rect.left(), y, rect.left() + drawn, y)
        painter.end()


class Led(QWidget):
    """`.pill .led`. 6px dot; `live` adds the design's 3px glow ring.

    A widget rather than a styled `QFrame` because the ring is a
    `box-shadow`, and the widget is 12px square so the ring has somewhere
    to land — the pill's left padding and spacing are pulled in by 3px
    each to put the dot back where the mockup has it.
    """

    DOT = 6
    RING = 3

    def __init__(self, mode: str) -> None:
        super().__init__()
        self._mode = mode
        self._state = "idle"
        self.setFixedSize(self.DOT + 2 * self.RING, self.DOT + 2 * self.RING)

    def set_state(self, state: str) -> None:
        self._state = state
        self.update()

    def paintEvent(self, _event) -> None:
        tokens = theme.TOKENS[self._mode]
        colour = {"live": tokens["ok"], "dead": tokens["crit"]}.get(self._state, tokens["dim"])
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        centre = QRectF(self.rect()).center()
        if self._state == "live":
            painter.setBrush(_alpha(tokens["ok"], 0.22))
            radius = self.DOT / 2 + self.RING
            painter.drawEllipse(centre, radius, radius)
        painter.setBrush(QColor(colour))
        painter.drawEllipse(centre, self.DOT / 2, self.DOT / 2)
        painter.end()


class RowActions(QWidget):
    """`.rowacts`: opacity 0 until the row is hovered or something in it
    has focus.

    Qt has no `:hover`/`:focus-within` selector that reaches a *child*, so
    the reveal is an opacity effect driven by `HoverRow`. Opacity, not
    `setVisible`: the design reserves the space either way, and a hidden
    widget is not in the tab order — the affordance has to stay keyboard
    reachable, which is why `HoverRow` also watches focus.
    """

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._effect = QGraphicsOpacityEffect(self)
        self._effect.setOpacity(0.0)
        self.setGraphicsEffect(self._effect)

    def add(self, button: QPushButton) -> None:
        self.layout().addWidget(button)

    def set_revealed(self, revealed: bool) -> None:
        self._effect.setOpacity(1.0 if revealed else 0.0)

    def is_revealed(self) -> bool:
        return self._effect.opacity() > 0.0


class HoverRow(QFrame):
    """A `.srow`/`.hyp` that reveals its `RowActions` on hover or focus."""

    def __init__(self) -> None:
        super().__init__()
        self.actions = RowActions()
        self._focused = 0

    def add_action(self, button: QPushButton) -> None:
        button.installEventFilter(self)
        self.actions.add(button)

    def enterEvent(self, event) -> None:
        self.actions.set_revealed(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.actions.set_revealed(bool(self._focused))
        super().leaveEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.FocusIn:
            self._focused += 1
            self.actions.set_revealed(True)
        elif event.type() == QEvent.FocusOut:
            self._focused = max(0, self._focused - 1)
            if not self._focused and not self.underMouse():
                self.actions.set_revealed(False)
        return False


class ToolButton(QPushButton):
    """`.btn`, optionally carrying a `.k` keyboard chip.

    The chip is a bordered child label, so the button's own sizeHint has
    to make room for it; `text-align: left` in the sheet keeps the label
    from centring itself underneath the chip.
    """

    def __init__(self, text: str, variant: str, chip: str | None = None) -> None:
        super().__init__(text)
        self.setObjectName(variant)
        self._chip: QLabel | None = None
        if chip:
            self._chip = QLabel(chip, self)
            self._chip.setObjectName("k_on_accent" if variant == "btn_primary" else "k")
            # Otherwise the chip swallows clicks aimed at the button.
            self._chip.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            layout = QHBoxLayout(self)
            # Qt applies a QPushButton's stylesheet padding when it draws
            # the label, not to `contentsRect`, so a child layout has to
            # be told the gutter itself or the chip lands on the border.
            layout.setContentsMargins(0, 0, BTN_PAD_X, 0)
            layout.setSpacing(0)
            layout.addStretch(1)
            layout.addWidget(self._chip, 0, Qt.AlignVCenter)

    def sizeHint(self):
        hint = super().sizeHint()
        if self._chip is not None:
            hint.setWidth(hint.width() + self._chip.sizeHint().width() + CHIP_GAP)
        return hint


class BurnMeter(QWidget):
    """The signature component.

    The track is a fixed cool-to-hot gradient and the fill is a mask that
    retracts from the left, so the colour at the leading edge is a direct
    read of how much budget is gone. Over budget, the mask is dropped and
    the whole track burns.

    The ramp is the design's own `--meter-*` trio, not accent/warn/crit:
    those coincide in dark mode, which is why substituting them looked
    right there and wrong in light.
    """

    def __init__(self, mode: str = "dark") -> None:
        super().__init__()
        self._mode = mode
        self._meter: MeterView | None = None
        self.setFixedHeight(TRACK_H)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_meter(self, meter: MeterView | None) -> None:
        self._meter = meter
        self.update()

    def paintEvent(self, _event) -> None:
        if self._meter is None:
            return
        tokens = theme.TOKENS[self._mode]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        radius = TRACK_H / 2
        track = QPainterPath()
        track.addRoundedRect(QRectF(self.rect()), radius, radius)
        # `.track { overflow: hidden }` — without the clip the mask and the
        # tick paint square corners over the rounded ones.
        painter.setClipPath(track)

        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0.00, QColor(tokens["meter_cool"]))
        gradient.setColorAt(0.38, QColor(tokens["meter_cool"]))
        gradient.setColorAt(0.76, QColor(tokens["meter_warm"]))
        gradient.setColorAt(1.00, QColor(tokens["meter_hot"]))
        painter.fillPath(track, gradient)

        if self._meter.over_s <= 0.0:
            edge = int(self.width() * self._meter.fraction)
            painter.fillRect(edge, 0, self.width() - edge, self.height(),
                             QColor(tokens["raised"]))
            painter.setPen(_alpha(tokens["text"], 0.22))
            painter.drawLine(edge, 0, edge, self.height())

        # `.tick` sits above the mask (z-index 2), so it is drawn last.
        painter.setPen(_alpha(tokens["text"], 0.30))
        tick_x = int(self.width() * CHECKPOINT_FRACTION)
        painter.drawLine(tick_x, 0, tick_x, self.height())
        painter.end()


class MeterScale(QWidget):
    """`.scale`: `0` at the left, the checkpoint at 75%, the budget at the
    right. Absolutely positioned in the design, so positioned by hand
    here — no Qt layout places a label by fraction of its parent."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(SCALE_H)
        self._start = self._make("0")
        self._mid = self._make("")
        self._end = self._make("")

    def _make(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("scale")
        return label

    def set_meter(self, meter: MeterView | None) -> None:
        self.setVisible(meter is not None)
        if meter is None:
            return
        percent = int(round(CHECKPOINT_FRACTION * 100))
        self._mid.setText(f"{percent}% · {format_mmss(meter.budget_s * CHECKPOINT_FRACTION)}")
        self._end.setText(format_mmss(meter.budget_s))
        for label in (self._start, self._mid, self._end):
            label.ensurePolished()  # the sheet's 10px mono, before measuring
            label.adjustSize()
        self._place()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place()

    def _place(self) -> None:
        self._start.move(0, 0)
        mid_x = int(self.width() * CHECKPOINT_FRACTION - self._mid.width() / 2)
        self._mid.move(max(0, mid_x), 0)
        self._end.move(max(0, self.width() - self._end.width()), 0)


class StackBar(QFrame):
    """Paused parents above, active last. Row buttons appear on hover or
    focus so the list reads as data first — never the only way to reach an
    action, which is why every one of them is also in the menu bar."""

    resume_requested = Signal(int)

    def __init__(self, mode: str = "dark") -> None:
        super().__init__()
        self._mode = mode
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(3)  # .stack { gap: 3px }

    def bind(self, dashboard: DashboardView) -> None:
        _clear(self._layout)
        for row in dashboard.stack:
            self._layout.addWidget(self._row(row, dashboard))

    def _row(self, row, dashboard: DashboardView) -> QFrame:
        line = HoverRow()
        line.setObjectName("srow_active" if row.active else "srow")
        layout = QHBoxLayout(line)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(ROW_GAP)

        mark = QLabel("▶" if row.active else "⏸")
        mark.setObjectName("srow_mark_active" if row.active else "srow_mark")
        mark.setFixedWidth(MARK_W)
        layout.addWidget(mark)

        loop_id = QLabel(f"#{row.loop_id}")
        loop_id.setObjectName("srow_id")
        loop_id.setFixedWidth(ID_W)
        layout.addWidget(loop_id)

        question = ElidedLabel(row.question)
        question.setObjectName("srow_q_active" if row.active else "srow_q")
        layout.addWidget(question, 1)

        meta_text = f"{format_duration(row.elapsed_s)} active"
        if not row.active:
            meta_text += f" · paused {format_duration(row.paused_s)}"
            if row.stale:
                meta_text += "  ⚠"
        meta = QLabel(meta_text)
        # `.meta .stale` colours the overdue part warn; the string is one
        # label, so the whole meta carries the colour rather than being
        # split — splitting it would change what the row reads as.
        meta.setObjectName("srow_meta_stale" if row.stale else "srow_meta")
        layout.addWidget(meta)

        if not row.active and "resume" in dashboard.enabled_actions:
            button = QPushButton("Resume")
            button.setObjectName("btn_sm")
            button.clicked.connect(
                lambda _checked=False, loop_id=row.loop_id:
                self.resume_requested.emit(loop_id)
            )
            line.add_action(button)
        layout.addWidget(line.actions)
        return line


class HypothesisList(QFrame):
    kill_requested = Signal(int)

    def __init__(self, mode: str = "dark") -> None:
        super().__init__()
        self._mode = mode
        self.setObjectName("col")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)  # .col { gap: 10px }
        self._count = QLabel()
        self._layout.addLayout(_colhead("hypotheses", self._count))
        self._rows = QVBoxLayout()
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(2)  # .hyps { gap: 2px }
        self._layout.addLayout(self._rows)
        self._layout.addStretch(1)

    def bind(self, dashboard: DashboardView) -> None:
        _clear(self._rows)
        self._count.setText(
            f"{dashboard.live_count} live · {dashboard.dead_count} ruled out"
        )
        for row in dashboard.hypotheses:
            self._rows.addWidget(self._row(row, dashboard))

    def _row(self, row, dashboard: DashboardView) -> QFrame:
        line = HoverRow()
        line.setObjectName("hyp")
        layout = QHBoxLayout(line)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(ROW_GAP)

        mark = QLabel(str(row.hyp_id) if row.alive else "✗")
        mark.setObjectName("hyp_mark" if row.alive else "hyp_mark_dead")
        mark.setFixedWidth(MARK_W)
        layout.addWidget(mark)

        if row.alive:
            text = ElidedLabel(row.text)
            text.setObjectName("hyp_text")
        else:
            # Kept visible on purpose: ruled-out hypotheses are the
            # evidence the loop is converging.
            text = StruckLabel(row.text, self._mode)
            text.setObjectName("hyp_text_dead")
        layout.addWidget(text, 1)

        if row.alive and "hyp_kill" in dashboard.enabled_actions:
            button = QPushButton("Rule out")
            button.setObjectName("btn_sm")
            button.clicked.connect(
                lambda _checked=False, hyp_id=row.hyp_id:
                self.kill_requested.emit(hyp_id)
            )
            line.add_action(button)
        layout.addWidget(line.actions)
        return line


class ActionLog(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("col_right")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(10)
        self._count = QLabel()
        self._layout.addLayout(_colhead("actions", self._count))
        self._entries = QVBoxLayout()
        self._entries.setContentsMargins(0, 0, 0, 0)
        self._entries.setSpacing(9)  # .log { gap: 9px }
        self._layout.addLayout(self._entries)
        self._layout.addStretch(1)

    def bind(self, dashboard: DashboardView) -> None:
        _clear(self._entries)
        self._count.setText(f"{len(dashboard.timeline)} entries")
        for entry in dashboard.timeline:
            self._entries.addWidget(_entry(entry))


def _entry(entry: timeline.Entry) -> QFrame:
    """`.entry` — a 46px time column and the content beside it. The
    timestamp is the half of `timeline.Entry` the window used to drop."""
    row = QFrame()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(ENTRY_GAP)

    when = QLabel(format_clock(entry.ts))
    when.setObjectName("entry_time")
    when.setFixedWidth(TIME_W)
    layout.addWidget(when, 0, Qt.AlignTop)

    what = QVBoxLayout()
    what.setContentsMargins(0, 0, 0, 0)
    what.setSpacing(1)  # .what { gap: 1px }
    text = QLabel(entry.text)
    text.setObjectName(ENTRY_STYLES.get(entry.kind, "entry_text"))
    text.setWordWrap(True)
    what.addWidget(text)
    if entry.because:
        because = QLabel(entry.because)
        because.setObjectName("because")
        because.setWordWrap(True)
        what.addWidget(because)
    layout.addLayout(what, 1)
    return row


class ThrashBanner(QFrame):
    """Takes vertical space and pushes the body down. An alarm that costs
    nothing is an alarm you stop seeing."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("thrash")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)  # .banner { gap: 12px }
        glyph = QLabel(THRASH_GLYPH)
        glyph.setObjectName("thrash_glyph")
        layout.addWidget(glyph, 0, Qt.AlignTop)
        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(2)  # .body { gap: 2px }
        self._labels = []
        for name in ("thrash_title", "thrash_why", "thrash_fix"):
            label = QLabel()
            label.setObjectName(name)
            label.setWordWrap(name == "thrash_fix")
            body.addWidget(label)
            self._labels.append(label)
        layout.addLayout(body, 1)

    def bind(self, dashboard: DashboardView) -> None:
        self.setVisible(dashboard.thrash is not None)
        if dashboard.thrash is None:
            return
        for label, text in zip(
            self._labels,
            (dashboard.thrash.title, dashboard.thrash.why, dashboard.thrash.fix),
        ):
            label.setText(text)


class ErrorBanner(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("error")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(11)  # .error { gap: 11px }
        glyph = QLabel(ERROR_GLYPH)
        glyph.setObjectName("error_glyph")
        layout.addWidget(glyph, 0, Qt.AlignTop)
        body = QVBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(1)  # .body { gap: 1px }
        self._text = QLabel(
            "Event log stopped parsing. Showing the last state that folded "
            "cleanly. Actions are disabled until the file is repaired."
        )
        self._text.setObjectName("error_text")
        self._text.setWordWrap(True)
        body.addWidget(self._text)
        self._path = QLabel()
        self._path.setObjectName("path")
        self._path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        body.addWidget(self._path)
        layout.addLayout(body, 1)

    def bind(self, dashboard: DashboardView) -> None:
        self.setVisible(dashboard.error is not None)
        if dashboard.error is None:
            return
        self._path.setText(dashboard.error)


class StatusStrip(QFrame):
    """`.strip`: a pill with an LED for the check-in state, then the
    numbers, then whatever the window hangs off the right."""

    def __init__(self, mode: str = "dark") -> None:
        super().__init__()
        self.setObjectName("strip")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)  # .strip { gap: 16px }

        self._pill = QFrame()
        self._pill.setObjectName("pill")
        pill_layout = QHBoxLayout(self._pill)
        pill_layout.setContentsMargins(0, 0, 0, 0)
        # 3px, not the design's 6px gap: `Led` is 12px wide so its ring has
        # room, and the dot is centred in it — 3 + 3 puts the dot's edge
        # exactly 6px from the text.
        pill_layout.setSpacing(3)
        self._led = Led(mode)
        pill_layout.addWidget(self._led)
        self._pill_text = QLabel()
        self._pill_text.setObjectName("pill_text")
        pill_layout.addWidget(self._pill_text)
        self._layout.addWidget(self._pill)

        self._next = QLabel()
        self._next.setObjectName("strip_text")
        self._layout.addWidget(self._next)
        self._depth = QLabel()
        self._depth.setObjectName("strip_text")
        self._layout.addWidget(self._depth)
        self._layout.addStretch(1)  # .spacer { margin-left: auto }

    def add_trailing(self, widget: QWidget) -> None:
        self._layout.addWidget(widget)

    def bind(self, dashboard: DashboardView) -> None:
        state = {"armed": "live", "unreadable": "dead"}.get(dashboard.status.state, "idle")
        self._led.set_state(state)
        self._pill_text.setText(dashboard.status.detail)
        _restyle(self._pill, {"live": "pill_live", "dead": "pill_dead"}.get(state, "pill"))
        _restyle(self._pill_text, {
            "live": "pill_text_live", "dead": "pill_text_dead",
        }.get(state, "pill_text"))

        meter = dashboard.meter
        has_next = meter is not None and meter.next_checkin_s is not None
        self._next.setText(f"next {format_duration(meter.next_checkin_s)}" if has_next else "")
        self._next.setVisible(has_next)
        self._depth.setText(f"depth {dashboard.status.depth}")
        self._depth.setVisible(dashboard.status.depth > 1)


def _colhead(caption: str, count: QLabel) -> QHBoxLayout:
    """`.colhead` — the design's caption on the left, the count on the
    right. Only the count was drawn before."""
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    label = QLabel(caption)
    label.setObjectName("label")
    count.setObjectName("count")
    layout.addWidget(label)
    layout.addStretch(1)
    layout.addWidget(count)
    return layout


def _restyle(widget: QWidget, name: str) -> None:
    """Qt caches a widget's resolved style at polish time and will not
    re-derive it just because `objectName` changed."""
    if widget.objectName() == name:
        return
    widget.setObjectName(name)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())
