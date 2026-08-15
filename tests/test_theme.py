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
