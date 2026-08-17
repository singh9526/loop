"""Whether Qt actually understands the fallback chains in `gui/theme.py`.

Not a test of what is installed on the machine running the suite — that
varies by platform, and this machine has none of `theme.MONO`'s or
`theme.UI`'s named faces at all (`QFontDatabase.families()` does not
contain "SF Mono", "Cascadia Mono", "Consolas", "Segoe UI", or "Segoe UI
Variable Text" under the offscreen platform here — checked directly
while auditing Task 17). That is expected on this box and says nothing
about a real Mac or a real Windows machine, where the platform-native
entries should resolve.

What this pins is one specific, platform-independent fact about Qt's own
style-sheet engine, verified empirically (not assumed) while auditing
whether `theme.py`'s Windows faces would have a sane fallback: the bare,
unquoted `monospace` / `sans-serif` tokens at the end of `theme.MONO` /
`theme.UI` are **not** understood as CSS generic-family keywords the way
a browser understands them. Qt's `font-family` parser treats every entry
in the list as a literal face name to search for; when none resolve, the
whole chain falls through to Qt's default application font — not to any
platform monospace/sans-serif default. Confirmed by comparing a
font-family list of two nonexistent names against the same list plus the
generic keyword: if Qt understood the keyword, the two would resolve
differently, and they do not.

In practice this is low-risk: `Consolas` and `Segoe UI` ship with every
Windows version in support, so on real Windows the chain should resolve
long before reaching the inert `monospace`/`sans-serif` tail. It matters
only in the fully-missing-face case the brief asks about ("a fallback to
a default serif means the family names ... are wrong for this Windows
version") — that fallback, when it happens, goes further than a serif:
it goes to Qt's default font, which need not even be monospaced.
`theme.MONO`/`theme.UI` are left untouched (constants keep their values):
this is a documented gap, not a fix.
"""

from __future__ import annotations

from PySide6.QtGui import QFontInfo
from PySide6.QtWidgets import QLabel

from loop.gui import theme


def _resolved_family(qapp, qss: str) -> str:
    label = QLabel("x")
    label.setStyleSheet(qss)
    label.ensurePolished()
    return QFontInfo(label.font()).family()


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
    Windows face, when SF Mono/SF Pro Text are absent) never gets a
    chance to be tried at all."""
    label = QLabel("x")
    label.setObjectName("clock")
    label.setStyleSheet(theme.stylesheet("dark"))
    label.ensurePolished()
    assert label.font().families() == [
        "SF Mono", "Cascadia Mono", "JetBrains Mono", "Consolas", "monospace",
    ]

    label2 = QLabel("x")
    label2.setStyleSheet(theme.stylesheet("dark"))
    label2.ensurePolished()
    assert label2.font().families() == [
        "SF Pro Text", "Segoe UI Variable Text", "Segoe UI", "sans-serif",
    ]
