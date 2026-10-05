# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
import pytest
import regex as regex_lib

from assay.schemas import EvaluationInput, TestTypeResult
from assay.worker.evaluators.engines import regex

DEFAULTS = {"mode": "search", "timeout_seconds": 1}


def _evaluation(answer, pattern=r"\d{4}-\d{2}-\d{2}", settings=DEFAULTS):
    config = {"pattern": pattern} if pattern is not None else {}
    return EvaluationInput(input="When?", reference=None, answer=answer, config=config,
                           engine_settings=settings)


def test_search_matches_anywhere_in_the_answer():
    result = regex.evaluate(_evaluation("Released on 2026-09-27, see notes"))

    assert result == TestTypeResult(passed=True, score=None, detail=None)


def test_search_miss_fails_with_no_score_and_names_the_mode():
    result = regex.evaluate(_evaluation("Released last week"))

    assert (result.passed, result.score) == (False, None)
    assert result.detail == "Pattern did not search the answer"


def test_fullmatch_requires_the_whole_answer():
    settings = {"mode": "fullmatch", "timeout_seconds": 1}

    assert regex.evaluate(_evaluation("2026-09-27", settings=settings)).passed is True
    miss = regex.evaluate(_evaluation("on 2026-09-27", settings=settings))
    assert miss.passed is False
    assert miss.detail == "Pattern did not fullmatch the answer"


def test_anchors_in_the_pattern_still_give_a_whole_answer_match_under_search():
    assert regex.evaluate(_evaluation("2026-09-27", pattern=r"^\d{4}-\d{2}-\d{2}$")).passed
    assert not regex.evaluate(_evaluation("on 2026-09-27", pattern=r"^\d{4}-\d{2}-\d{2}$")).passed


def test_flags_live_in_the_users_pattern():
    assert regex.evaluate(_evaluation("SETTINGS", pattern="settings")).passed is False
    assert regex.evaluate(_evaluation("SETTINGS", pattern="(?i)settings")).passed is True


def test_an_invalid_pattern_fails_this_type_with_the_compilers_message():
    with pytest.raises(regex_lib.error, match="missing \\)"):
        regex.evaluate(_evaluation("anything", pattern="(unclosed"))


def test_a_catastrophic_pattern_hits_the_timeout_and_says_so():
    # (a|aa)+$ against a long run of a's ending in b is exponential even for
    # the regex library; a short timeout keeps the test fast and is the bound.
    settings = {"mode": "search", "timeout_seconds": 0.2}

    result = regex.evaluate(_evaluation("a" * 45 + "b", pattern=r"(a|aa)+$", settings=settings))

    assert (result.passed, result.score) == (False, None)
    assert result.detail == (
        "Pattern took longer than 0.2 s to match — likely catastrophic backtracking"
    )


def test_settings_default_to_search_with_a_one_second_timeout():
    assert regex.evaluate(_evaluation("on 2026-09-27", settings={})).passed is True


def test_an_unknown_mode_is_a_catalogue_error():
    with pytest.raises(ValueError, match="Unknown regex mode 'match'"):
        regex.evaluate(_evaluation("x", settings={"mode": "match", "timeout_seconds": 1}))


@pytest.mark.parametrize("pattern", [None, ""])
def test_a_missing_or_empty_pattern_is_this_types_failure(pattern):
    with pytest.raises(ValueError, match="No pattern configured"):
        regex.evaluate(_evaluation("anything", pattern=pattern))


# --- negate: "Regex Must Not Match" ---

NEGATED = {"mode": "search", "timeout_seconds": 1, "negate": True}
CARD_NUMBER = r"\b(?:\d[ -]?){13,16}\b"


def test_negated_passes_when_the_pattern_is_absent():
    result = regex.evaluate(_evaluation("Your order ships Monday.", pattern=CARD_NUMBER,
                                        settings=NEGATED))

    assert result == TestTypeResult(passed=True, score=None, detail=None)


def test_negated_fails_when_the_pattern_is_found_anywhere():
    result = regex.evaluate(_evaluation("Card on file: 4111 1111 1111 1111, thanks.",
                                        pattern=CARD_NUMBER, settings=NEGATED))

    assert result == TestTypeResult(passed=False, score=None,
                                    detail="Forbidden pattern found in the answer")


def test_negated_fullmatch_only_fails_when_the_whole_answer_matches():
    settings = {"mode": "fullmatch", "timeout_seconds": 1, "negate": True}

    assert regex.evaluate(_evaluation("N/A", pattern=r"N/A", settings=settings)) == \
        TestTypeResult(passed=False, score=None,
                       detail="The whole answer matches the forbidden pattern")
    assert regex.evaluate(_evaluation("N/A for now", pattern=r"N/A",
                                      settings=settings)).passed is True


def test_negated_timeout_still_fails():
    settings = {"mode": "search", "timeout_seconds": 0.2, "negate": True}

    result = regex.evaluate(_evaluation("a" * 45 + "b", pattern=r"(a|aa)+$", settings=settings))

    assert result.passed is False
    assert result.detail.startswith("Pattern took longer than 0.2 s")


# --- normalize_lookalikes ---


def test_the_answer_is_made_plain_by_default():
    result = regex.evaluate(_evaluation("It’s due 2026‑10‑01",
                                        pattern=r"It's due \d{4}-\d{2}-\d{2}"))

    assert result.passed is True


def test_a_row_can_turn_normalize_lookalikes_off():
    settings = {**DEFAULTS, "normalize_lookalikes": False}

    result = regex.evaluate(_evaluation("It’s due", pattern="It's due",
                                        settings=settings))

    assert result.passed is False


def test_the_pattern_is_never_rewritten():
    # [a‐z] is three literal characters; rewritten to [a-z] it would be a range
    result = regex.evaluate(_evaluation("m", pattern="^[a‐z]$"))

    assert result.passed is False
