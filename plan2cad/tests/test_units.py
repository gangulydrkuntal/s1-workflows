import pytest

from plan2cad.units import FT, normalise, parse_length_ft, parse_length_m


@pytest.mark.parametrize("text,feet", [("14'7\"", 14 + 7 / 12), ("11'", 11.0), ("1'9\"", 1.75), ("7\"", 7 / 12),
                                       ("14'-7\"", 14 + 7 / 12), ("31'11\"", 31 + 11 / 12), ("60'", 60.0),
                                       ("14’7”", 14 + 7 / 12)])
def test_ftin_valid(text, feet):
    assert parse_length_ft(text) == pytest.approx(feet)


def test_ftin_exact_float_identity_with_exercise():
    # the golden reproduction depends on this being bit-identical to the exercise code (31 + 11/12)
    assert parse_length_ft("31'11\"") == 31 + 11 / 12
    assert parse_length_ft("60'") == 60.0


@pytest.mark.parametrize("bad", ["14", "abc", "", "'", "14'x", "1.2.3'"])
def test_ftin_invalid(bad):
    with pytest.raises(ValueError):
        parse_length_ft(bad)


def test_metric():
    assert parse_length_m("4500", "mm") == pytest.approx(4.5)
    assert parse_length_m("4.5", "m") == pytest.approx(4.5)
    assert parse_length_m("4.5 m", "mm") == pytest.approx(4.5)       # explicit unit wins
    assert parse_length_m("1783,92", "mm") == pytest.approx(1.78392)  # decimal comma, as printed on drawing 2
    assert parse_length_ft("3048", "mm") == pytest.approx(10.0)
    assert FT == 0.3048


def test_unknown_units_and_garbage():
    with pytest.raises(ValueError):
        parse_length_ft("5", "furlongs")
    with pytest.raises(ValueError):
        parse_length_ft("abc", "mm")


def test_normalise_quotes():
    assert normalise("14’7”") == "14'7\""
