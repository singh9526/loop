"""Whether Qt actually resolves the fallback chains in `gui/theme.py`.

The stacks these replaced passed every test in this suite while resolving
to the wrong face. `theme.MONO` led with `SF Mono`, which is not a
registered family name on macOS — it ships inside Terminal.app — so Qt
matched it to the proportional system face and rendered the burn clock
with variable-width digits. `theme.UI` led with `SF Pro Text`, also
unregistered, and landed in the same place. Both also cost a ~250ms
`Populating font family aliases` sweep on every launch.

Nothing caught it because nothing asked what the font *was*. A test that
only reads `font().families()` sees the list it was given; the bug is one
level down, in what the engine matched it to. So the tests below assert
on `QFontInfo`, which reports the resolved face, and on per-character
advances, which are equal iff the face really is fixed-pitch.

The trailing `monospace` / `sans-serif` keywords stay in both stacks for
documentation only: Qt's `font-family` parser treats every entry as a
literal family name and does not understand CSS generic families, which
the first test below pins. They are never reached, because the entries in
front of them resolve on both platforms.
"""

from __future__ import annotations

import sys

import pytest
from PySide6.QtGui import QFontInfo, QFontMetricsF
from PySide6.QtWidgets import QLabel

from loop.gui import theme


def _resolved(qss: str, object_name: str = "") -> QLabel:
    label = QLabel("x")
    if object_name:
        label.setObjectName(object_name)
    label.setStyleSheet(qss)
    label.ensurePolished()
    return label


def _resolved_family(qapp, qss: str) -> str:
    return QFontInfo(_resolved(qss).font()).family()


def test_qt_does_not_treat_the_generic_keyword_specially(qapp):
    with_generic = _resolved_family(
        qapp, 'QLabel { font-family: "loop-test-bogus-1", "loop-test-bogus-2", monospace; }'
    )
    without_generic = _resolved_family(
        qapp, 'QLabel { font-family: "loop-test-bogus-1", "loop-test-bogus-2"; }'
    )
    # Same outcome either way: the trailing keyword changed nothing, on
    # this platform's Qt. If a future Qt version starts honouring CSS
    # generic families, this assertion is the one that will tell us.
    assert with_generic == without_generic


def test_the_full_fallback_chain_reaches_the_font_in_order(qapp):
    """Whatever Qt does or does not match, the whole comma list from
    `theme.py` must reach the font engine in order — `QFont.setFamilies`,
    not just the first name — or a face two steps down the list (the
    Windows face, when the macOS one is absent) never gets a chance to be
    tried at all."""
    clock = _resolved(theme.stylesheet("dark"), "clock")
    assert clock.font().families() == [
        "Menlo", "Cascadia Mono", "JetBrains Mono", "Consolas", "monospace",
    ]

    body = _resolved(theme.stylesheet("dark"))
    assert body.font().families() == [
        ".AppleSystemUIFont", "Segoe UI Variable Text", "Segoe UI", "sans-serif",
    ]


def test_both_stacks_still_carry_the_windows_faces():
    """This app ships to Windows too, and neither Windows face exists on
    the machine that runs this suite — so nothing about how they resolve
    can be asserted here. That they are still *in* the chain can."""
    assert "Cascadia Mono" in theme.MONO and "Consolas" in theme.MONO
    assert "Segoe UI Variable Text" in theme.UI and "Segoe UI" in theme.UI


def test_neither_stack_names_a_css_keyword_qt_cannot_resolve():
    """The design's own stacks lead with `ui-monospace` and
    `-apple-system`. Copying them verbatim would put an unresolvable name
    at the head of each chain, which is the shape of the bug this file
    exists for."""
    for stack in (theme.MONO, theme.UI):
        for keyword in ("ui-monospace", "-apple-system", "system-ui"):
            assert keyword not in stack


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS face names")
def test_the_mono_stack_resolves_to_a_genuinely_fixed_pitch_face(qapp):
    """The whole point of `theme.MONO`. A proportional face passed every
    test this suite had, and gave the clock and the burn meter's numbers
    digits of four different widths.

    `fixedPitch` alone is not enough evidence — it is a flag the font
    declares — so the advances are measured too.
    """
    clock = _resolved(theme.stylesheet("dark"), "clock")
    info = QFontInfo(clock.font())
    assert info.family() == "Menlo"
    assert info.fixedPitch(), f"{info.family()!r} is not fixed pitch"

    metrics = QFontMetricsF(clock.font())
    advances = {char: metrics.horizontalAdvance(char) for char in "1i8W0 "}
    assert len(set(advances.values())) == 1, advances


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS face names")
def test_the_ui_stack_resolves_to_the_system_face_and_is_not_the_mono_one(qapp):
    """The previous stacks resolved *both* faces to `.AppleSystemUIFont`,
    so the dashboard had no typographic contrast at all between its prose
    and its numbers. Asserting they differ is what catches a regression
    to that state."""
    body = QFontInfo(_resolved(theme.stylesheet("dark")).font())
    clock = QFontInfo(_resolved(theme.stylesheet("dark"), "clock").font())
    assert body.family() == ".AppleSystemUIFont"
    assert not body.fixedPitch()
    assert body.family() != clock.family()
