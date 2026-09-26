import pytest
from pydantic import ValidationError

from assay.config import Settings


def test_log_level_is_normalized_to_upper_case():
    assert Settings(_env_file=None, log_level="debug").log_level == "DEBUG"


def test_unknown_log_level_is_rejected_at_startup():
    with pytest.raises(ValidationError, match="unknown log level 'LOUD'"):
        Settings(_env_file=None, log_level="LOUD")


def test_log_format_only_accepts_text_or_json():
    assert Settings(_env_file=None, log_format="json").log_format == "json"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_format="xml")
