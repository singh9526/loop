"""The dashboard against the design, measured.

There is no screen here, so every rule the design states as a number is
checked as a number: resolved fonts, palette colours, laid-out geometry,
and grabbed pixels. The tests that matter most are the ones covering
things that were silently wrong before and passed anyway — a "mono" face
that was proportional, a meter painted in the status colours instead of
its own ramp, and layouts running on Qt's 9px/6px defaults instead of the
design's rhythm.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QEnterEvent, QFontInfo, QKeySequence, QPalette
from PySide6.QtWidgets import QFrame, QLabel, QPushButton

import pytest

from loop.app import view
from loop.core import timeline
from loop.gui import theme
from loop.gui.widgets import RowActions, StruckLabel
from loop.gui.window import MainWindow, _clock_parts, _clock_text

pytestmark = pytest.mark.usefixtures("qapp")

TICK = 1_700_000_000.0


def _dashboard(**overrides) -> view.DashboardView:
    fields = dict(
        active_id=3,
        question="is the logging config filtering it?",
        stop_condition="a WARNING from the app logger reaches the console",
        meter=view.MeterView(elapsed_s=1122.0, budget_s=2700.0, fraction=0.415,
                             over_s=0.0, next_checkin_s=192.0),
        stack=(
            view.StackRow(1, "why is the stack trace empty?", False, 720.0, 2460.0, False),
            view.StackRow(3, "is the logging config filtering it?", True, 1122.0, 0.0, False),
        ),
        hypotheses=(
            view.HypothesisRow(1, "dictConfig disables existing loggers", True),
            view.HypothesisRow(2, "handler level is above ERROR", False),
        ),
        timeline=(
            timeline.Entry(TICK, timeline.ACTION, "added print in wrapper",
                           "will show the swallowed frame"),
            timeline.Entry(TICK + 420, timeline.RULED_OUT, "ruled out §2", "answered at the check-in"),
            timeline.Entry(TICK + 900, timeline.PING, "check-in: hypothesis space not smaller"),
        ),
        thrash=None,
        status=view.StatusView("armed", "check-ins armed", 3),
        enabled_actions=frozenset(view.ACTIVE_ACTIONS | {"open"}),
        live_count=1, dead_count=1,
    )
    fields.update(overrides)
    return view.DashboardView(**fields)


@pytest.fixture
def window(qapp, request):
    mode = getattr(request, "param", "dark")
    win = MainWindow(controller=None, mode=mode)
    win.setStyleSheet(theme.stylesheet(mode))
    win.resize(1100, 760)
    win.show()
    qapp.processEvents()
    win.bind(_dashboard())
    qapp.processEvents()
    yield win
    win.hide()


def _colour(widget) -> str:
    return widget.palette().color(QPalette.ColorRole.WindowText).name().lower()


def _page(window) -> QFrame:
    return window.centralWidget().widget(0)


# --- layout rhythm ---------------------------------------------------------

def test_no_layout_on_the_dashboard_runs_on_qts_default_margins(window):
    """Not one layout in `widgets.py` or `window.py` used to set its
    margins or spacing, so Qt's uniform 9px/6px applied everywhere and the
    design's rhythm was flat. Every container here sets 0 and lets the
    stylesheet's padding place things, so a layout still carrying the
    platform default is a layout somebody forgot."""
    offenders = []
    for layout in _page(window).findChildren(type(_page(window).layout())) + [_page(window).layout()]:
        margins = layout.contentsMargins()
        if (margins.left(), margins.top(), margins.right(), margins.bottom()) != (0, 0, 0, 0):
            offenders.append((layout.objectName() or layout.parentWidget(), margins))
    assert offenders == []


def test_the_two_columns_are_five_to_six_not_one_to_one(window):
    """`.cols` is `minmax(0, 5fr) minmax(0, 6fr)`. The build had 1:1."""
    ratio = window._log.width() / window._hypotheses.width()
    assert ratio == pytest.approx(6 / 5, abs=0.02)


def test_the_head_carries_the_designs_padding(window):
    """`.head { padding: 16px 18px 18px }`."""
    head = _page(window).findChild(QFrame, "head")
    margins = head.contentsRect()
    assert (margins.x(), margins.y()) == (18, 16)
    assert head.width() - margins.width() - margins.x() == 18
    assert head.height() - margins.height() - margins.y() == 18


def test_the_columns_are_separated_by_a_hairline_and_capped_by_one(window):
    """`.col + .col { border-left }` and `.cols { border-top }` — two
    hairlines, not a bordered card each."""
    cols = _page(window).findChild(QFrame, "cols")
    image = cols.grab().toImage()
    line = theme.TOKENS["dark"]["line"].lower()
    assert image.pixelColor(600, 0).name() == line
    assert image.pixelColor(window._log.x() - cols.x(), 40).name() == line


# --- the stack -------------------------------------------------------------

def test_the_active_row_is_tinted_and_barred_and_the_paused_rows_align(window):
    """`.srow.active` is `box-shadow: inset 2px 0 0 var(--accent)` over an
    11% accent tint. Qt has no box-shadow, so the bar is a real
    `border-left` and paused rows carry the same border in `transparent`
    — otherwise the two would be 2px out of alignment."""
    tokens = theme.TOKENS["dark"]
    active = window._stack.findChild(QFrame, "srow_active")
    image = active.grab().toImage()
    assert image.pixelColor(0, active.height() // 2).name() == tokens["accent"].lower()
    assert image.pixelColor(200, active.height() // 2).name() == tokens["active_tint"].lower()

    paused = window._stack.findChild(QFrame, "srow")
    assert paused.contentsRect().x() == active.contentsRect().x() == 7


def test_the_stack_row_columns_are_the_designs_grid(window):
    """`.srow` is `15px 34px 1fr auto auto` with a 9px gap."""
    row = window._stack.findChild(QFrame, "srow_active")
    mark = row.findChild(QLabel, "srow_mark_active")
    loop_id = row.findChild(QLabel, "srow_id")
    assert mark.width() == 15
    assert loop_id.width() == 34
    assert loop_id.x() - (mark.x() + mark.width()) == 9


def test_the_stop_condition_is_drawn_at_all(window):
    """`.stop` was absent entirely, though `DashboardView` has carried
    `stop_condition` since the view model was written."""
    assert window._stop.isVisible()
    assert window._stop_value.text() == "a WARNING from the app logger reaches the console"
    caption = window._stop.findChild(QLabel, "label")
    assert caption.text() == "stop when"

    window.bind(_dashboard(stop_condition=None))
    assert not window._stop.isVisible()


# --- the meter -------------------------------------------------------------

def test_the_clock_splits_into_the_designs_two_sizes_without_changing_the_string(window):
    """`.clock` is 25px and its `/ 45:00` suffix is 15px dim. Two labels,
    one string: the halves must still concatenate to what the window used
    to set on a single label."""
    dashboard = _dashboard()
    head, tail = _clock_parts(dashboard)
    assert head + tail == _clock_text(dashboard)
    assert window._clock.text() == "18:42"
    assert window._clock_of.text() == " / 45:00"
    assert window._clock.font().pixelSize() == 25
    assert window._clock_of.font().pixelSize() == 15
    assert _colour(window._clock_of) == theme.TOKENS["dark"]["dim"].lower()


def test_the_meter_top_row_names_the_next_check_in(window):
    """`.mtop`'s right half — `next check-in 3:12`, with the number in the
    text colour. It had no widget at all."""
    assert window._mright.text() == "next check-in"
    assert window._mright_value.text() == "3:12"
    assert _colour(window._mright_value) == theme.TOKENS["dark"]["text"].lower()

    window.bind(_dashboard(meter=view.MeterView(10.0, 60.0, 0.16, 0.0, None)))
    assert not window._mright_value.isVisible()


def test_the_scale_row_labels_zero_the_checkpoint_and_the_budget(window):
    """`.scale` — `0`, `75% · 33:45` centred on the tick, `45:00` flush
    right. Another row with no widget behind it."""
    labels = window._scale.findChildren(QLabel)
    assert [label.text() for label in labels] == ["0", "75% · 33:45", "45:00"]
    start, mid, end = labels
    assert start.x() == 0
    assert mid.x() + mid.width() / 2 == pytest.approx(window._scale.width() * 0.75, abs=1)
    assert end.x() + end.width() == window._scale.width()


@pytest.mark.parametrize("window", ["light", "dark"], indirect=True)
def test_the_track_burns_the_meter_ramp_not_the_status_colours(window):
    """`--meter-cool/warm/hot` are their own trio. In dark they happen to
    equal accent/warn/crit, which is why substituting those went unnoticed
    — in light they are three different values and the substitution put a
    text colour in the gradient."""
    mode = window._mode
    tokens = theme.TOKENS[mode]
    image = window._meter.grab().toImage()
    assert image.pixelColor(4, 4).name() == tokens["meter_cool"].lower()
    if mode == "light":
        assert tokens["meter_cool"] != tokens["accent"]


def test_the_unburnt_remainder_is_masked_and_the_overrun_is_not(window):
    tokens = theme.TOKENS["dark"]
    far = int(window._meter.width() * 0.9)
    assert window._meter.grab().toImage().pixelColor(far, 4).name() == tokens["raised"].lower()

    window.bind(_dashboard(meter=view.MeterView(2800.0, 2700.0, 1.0, 100.0, None)))
    window._meter.repaint()
    burnt = window._meter.grab().toImage().pixelColor(far, 4).name()
    assert burnt != tokens["raised"].lower()


# --- the columns -----------------------------------------------------------

def test_both_columns_have_the_designs_caption_beside_their_count(window):
    """`.colhead` is a caption and a count. Only the count was drawn."""
    captions = [label.text() for label in _page(window).findChildren(QLabel, "label")]
    assert "hypotheses" in captions and "actions" in captions
    counts = [label.text() for label in _page(window).findChildren(QLabel, "count")]
    assert "1 live · 1 ruled out" in counts and "3 entries" in counts


def test_a_ruled_out_hypothesis_is_struck_in_the_ok_colour(window):
    """`.hyp.dead .t` strikes through in `ok`, not in the text colour Qt
    would use — Qt has no `text-decoration-color`, so the line is painted
    rather than declared."""
    dead = window._hypotheses.findChild(QLabel, "hyp_text_dead")
    assert isinstance(dead, StruckLabel)
    assert _colour(dead) == theme.TOKENS["dark"]["dim"].lower()
    marker = window._hypotheses.findChild(QLabel, "hyp_mark_dead")
    assert _colour(marker) == theme.TOKENS["dark"]["ok"].lower()


def test_every_log_entry_shows_the_timestamp_the_window_used_to_drop(window):
    """`timeline.Entry.ts` was already there and `ActionLog.bind` threw it
    away. `.entry` is a `46px | 1fr` grid of time and content."""
    times = window._log.findChildren(QLabel, "entry_time")
    assert len(times) == 3
    assert all(label.width() == 46 for label in times)
    assert all(len(label.text()) == 5 and label.text()[2] == ":" for label in times)
    assert QFontInfo(times[0].font()).fixedPitch()


def test_the_killed_and_ping_entry_variants_are_coloured(window):
    """`.entry.killed` reads `ok`, `.entry.ping` reads `muted`. Neither
    existed."""
    tokens = theme.TOKENS["dark"]
    assert _colour(window._log.findChild(QLabel, "entry_killed")) == tokens["ok"].lower()
    assert _colour(window._log.findChild(QLabel, "entry_ping")) == tokens["muted"].lower()


def test_the_because_line_is_offset_by_its_own_hairline(window):
    """`.because` is `box-shadow: inset 1px 0 0` plus 10px of padding —
    a rule, not a shadow, so 1px of border and 9px of padding."""
    because = window._log.findChild(QLabel, "because")
    assert because.contentsRect().x() == 10


# --- the status strip ------------------------------------------------------

def test_the_strip_is_a_pill_and_a_led_not_one_joined_string(window):
    tokens = theme.TOKENS["dark"]
    assert window._strip.findChild(QFrame, "pill_live") is not None
    text = window._strip.findChild(QLabel, "pill_text_live")
    assert text.text() == "check-ins armed"
    assert _colour(text) == tokens["ok"].lower()


def test_the_pill_follows_the_status_through_all_three_states(window):
    window.bind(_dashboard(status=view.StatusView("unreadable", "frozen at 12m", 1),
                           error="~/.loop/events.jsonl:214"))
    assert window._strip.findChild(QFrame, "pill_dead") is not None
    assert window._error.isVisible()

    window.bind(_dashboard(meter=None, status=view.StatusView("idle", "no check-ins scheduled", 1)))
    assert window._strip.findChild(QFrame, "pill") is not None


# --- the toolbar -----------------------------------------------------------

def test_the_toolbar_has_the_designs_gap_between_its_two_groups(window):
    """`.toolbar .gap { margin-left: auto }` — the primary actions on the
    left, the ones that change the loop's shape or end it on the right."""
    row = window._toolbar.layout()
    gap = row.itemAt(4)
    assert gap.widget() is None and gap.spacerItem() is not None
    assert window._buttons["pause"].x() < gap.geometry().x() < window._buttons["cut"].x()


def test_the_shortcut_buttons_carry_a_keyboard_chip_that_names_a_real_binding(window):
    """`.k` on a button that has no shortcut would be a lie, so the two
    the design chips are bound on the QAction too — which also puts them
    in the menu, reachable without the toolbar."""
    for name in ("try", "open"):
        chip = window._buttons[name].findChild(QLabel)
        assert chip is not None and chip.text()
        assert chip.text() == window._actions[name].shortcut().toString(
            QKeySequence.NativeText)
    assert window._buttons["pause"].findChild(QLabel) is None
    assert window._buttons["try"].objectName() == "btn_primary"
    assert window._buttons["abandon"].objectName() == "btn_danger"


def test_a_chip_never_lands_on_top_of_the_label_it_belongs_to(window):
    """A QPushButton centres its own text, so a chip pinned to the right
    edge overlaps it unless the sheet left-aligns the label and the chip's
    layout is told the button's gutter — Qt applies stylesheet padding
    when it draws the label, not to `contentsRect`."""
    from PySide6.QtGui import QFontMetricsF

    for name in ("try", "open"):
        button = window._buttons[name]
        chip = button.findChild(QLabel)
        text_right = 12 + QFontMetricsF(button.font()).horizontalAdvance(button.text())
        assert text_right < chip.x(), name
        assert button.width() - (chip.x() + chip.width()) == 12, name


# --- hover reveal ----------------------------------------------------------

def test_row_actions_are_hidden_until_hovered_and_stay_keyboard_reachable(window):
    """`.rowacts` is `opacity: 0` until `:hover`/`:focus-within`. Qt has
    neither selector for a child, so it is an opacity effect — opacity and
    not `setVisible`, because a hidden widget leaves the tab order and the
    design's own note is that no row action is mouse-only."""
    row = window._hypotheses.findChild(QFrame, "hyp")
    actions = row.findChild(RowActions)
    button = row.findChild(QPushButton)
    assert button is not None and button.text() == "Rule out"
    assert not actions.is_revealed()

    row.enterEvent(QEnterEvent(QPoint(1, 1), QPoint(1, 1), QPoint(1, 1)))
    assert actions.is_revealed()
    row.leaveEvent(QEvent(QEvent.Leave))
    assert not actions.is_revealed()

    button.setFocus(Qt.TabFocusReason)
    assert actions.is_revealed(), "a keyboard user must be able to see it"
    button.clearFocus()
    assert not actions.is_revealed()


# --- typography ------------------------------------------------------------

def test_everything_the_design_sets_in_mono_resolves_to_a_fixed_pitch_face(window):
    """The single largest cause of the mismatch: `theme.MONO` resolved to
    the proportional system face, so nothing on the dashboard was
    actually monospaced and every "tabular" number was not."""
    names = ("clock", "clock_of", "mright", "mright_value", "scale", "label",
             "count", "srow_id", "srow_meta", "entry_time", "path", "pill_text_live")
    seen = 0
    for name in names:
        for label in _page(window).findChildren(QLabel, name):
            info = QFontInfo(label.font())
            assert info.fixedPitch(), f"#{name} resolved to {info.family()!r}"
            seen += 1
    assert seen >= len(names) - 2  # #path only exists while the log is unreadable


def test_prose_is_not_set_in_the_mono_face(window):
    """The two stacks used to resolve to the same face, which left the
    dashboard with no contrast between its prose and its numbers."""
    for name in ("srow_q_active", "hyp_text", "entry_text", "stop_value"):
        label = _page(window).findChild(QLabel, name)
        assert label is not None, name
        assert not QFontInfo(label.font()).fixedPitch(), name
