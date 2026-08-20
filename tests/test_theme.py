import re

from loop.gui import theme


def test_both_modes_define_the_same_token_names():
    assert set(theme.TOKENS["light"]) == set(theme.TOKENS["dark"])


def test_every_token_is_a_hex_colour():
    for mode in theme.TOKENS.values():
        for value in mode.values():
            assert value.startswith("#") and len(value) == 7


def test_the_stylesheet_substitutes_the_mode_and_leaves_no_placeholders():
    light = theme.stylesheet("light")
    dark = theme.stylesheet("dark")
    assert theme.TOKENS["light"]["panel"] in light
    assert theme.TOKENS["dark"]["panel"] in dark
    assert light != dark
    assert "%(" not in light  # format slots do not survive
    assert "{" in light and "}" in light  # QSS braces survive


def test_an_unknown_mode_refuses_rather_than_rendering_half_a_theme():
    import pytest
    with pytest.raises(KeyError):
        theme.stylesheet("sepia")


def test_the_overlay_scale_leaves_the_dashboard_scale_alone():
    """`theme.py` is shared. The over-budget clock reads `#clock`'s size
    and the logbook's match headers read `#question`'s; the check-in's
    display sizes must not have leaked into either.

    `#clock` is 25px, not the 26px this pinned before: the design's
    `.clock` is 25px and the app had been built from a description of it.
    Weight goes with it — the design sets none, so the clock is regular.
    """
    qss = theme.stylesheet("dark")
    assert "QLabel#question { font-size: 18px; font-weight: 600; }" in qss
    assert "QLabel#clock, QLabel#over { font-family:" in qss
    assert "font-size: 25px; }" in qss
    assert "font-size: 10px; letter-spacing: 1px; text-transform: uppercase; }" in qss
    assert "QLabel#overlay_question { font-size: 32px; font-weight: 600; }" in qss


def test_the_meter_ramp_is_its_own_trio_in_both_modes():
    """The design carries `--meter-cool/warm/hot` separately from
    accent/warn/crit. They coincide in dark, which is why substituting the
    status colours for them passed unnoticed — and are three different
    values in light, where the substitution was simply wrong."""
    light, dark = theme.TOKENS["light"], theme.TOKENS["dark"]
    assert (light["meter_cool"], light["meter_warm"], light["meter_hot"]) == (
        "#17998F", "#C98A1E", "#C04630")
    assert light["meter_cool"] != light["accent"]
    assert light["meter_warm"] != light["warn"]
    assert (dark["meter_cool"], dark["meter_warm"], dark["meter_hot"]) == (
        "#5AD1C8", "#E4A33C", "#E4644E")


def test_every_object_name_the_sheet_styles_is_one_something_sets():
    """`QLabel#over` and `QLabel#banner` were both written against object
    names nothing assigned, twice. A rule keyed on a name no widget
    carries is dead code that reads as a working style.

    The scan is textual on purpose: it needs no Qt, so it runs on a
    machine without PySide6 alongside the rest of this file.
    """
    from pathlib import Path

    gui = Path(__file__).resolve().parent.parent / "loop" / "gui"
    assigned = set()
    for path in gui.rglob("*.py"):
        # Every bare-identifier string literal, not just the argument of a
        # literal `setObjectName("x")`: half these names reach the call
        # through a variant table (`TOOLBAR_VARIANTS`, `ENTRY_STYLES`) or a
        # conditional. The looser scan still fails on a name that appears
        # nowhere in `gui/` at all, which is exactly the bug it guards.
        assigned.update(re.findall(r"[\"']([A-Za-z_][\w]*)[\"']",
                                   path.read_text(encoding="utf-8")))

    styled = set(re.findall(r"#([A-Za-z_][\w]*)\s*(?=[,\s{:])", theme.stylesheet("dark")))
    styled -= set(re.findall(r"#[0-9A-Fa-f]{6}\b", theme.stylesheet("dark")))
    assert styled <= assigned, f"styled but never set: {sorted(styled - assigned)}"


def test_the_overlay_scale_uppercases_nothing():
    """The check-in draws product strings. `#label` transforms; none of the
    overlay rules may."""
    qss = re.sub(r"/\*.*?\*/", "", theme.stylesheet("dark"), flags=re.DOTALL)
    for rule in qss.split("}"):
        if "#overlay_" in rule:
            assert "text-transform" not in rule, rule


def test_the_overlay_scale_adds_no_colour_and_no_face():
    """Sizes only: every colour in the sheet is a token, and the only two
    font stacks are the ones the dashboard already uses."""
    for mode, tokens in theme.TOKENS.items():
        qss = theme.stylesheet(mode)
        assert set(re.findall(r"#[0-9A-Fa-f]{6}\b", qss)) <= set(tokens.values())
        assert set(re.findall(r"font-family: ([^;]+);", qss)) <= {theme.MONO, theme.UI}
