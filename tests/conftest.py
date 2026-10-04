import pytest

import assay.config


@pytest.fixture(autouse=True)
def no_env_file(monkeypatch, tmp_path):
    """Keeps the developer's own .env out of every test: a secret looked up
    by name (config.environment_value) comes from the test's environment
    only, unless a test points it at a .env of its own."""
    monkeypatch.setattr(assay.config, "_ENV_FILE", tmp_path / "no.env")
