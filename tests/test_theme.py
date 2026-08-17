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
    """`theme.py` is shared. Task 11's over-budget clock reads `#clock`'s
    26px and the dashboard question is 18px; the check-in's display sizes
    must not have leaked into either."""
    qss = theme.stylesheet("dark")
    assert "QLabel#question { font-size: 18px; font-weight: 600; }" in qss
    assert "QLabel#clock, QLabel#over { font-family:" in qss
    assert "font-size: 26px; font-weight: 600; }" in qss
    assert "font-size: 10px; letter-spacing: 1px; text-transform: uppercase; }" in qss
    assert "QLabel#overlay_question { font-size: 32px; font-weight: 600; }" in qss


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
