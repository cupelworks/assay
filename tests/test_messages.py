import pytest

from assay.messages import sentence


@pytest.mark.parametrize("text,expected", [
    ("unterminated character set at position 5", "Unterminated character set at position 5"),
    ("Already a sentence", "Already a sentence"),
    ("'Bad Name' is not a valid header name", "'Bad Name' is not a valid header name"),
    ("", ""),
])
def test_only_the_first_letter_is_capitalized(text, expected):
    assert sentence(text) == expected
