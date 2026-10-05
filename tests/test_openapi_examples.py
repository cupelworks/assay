# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Francesco Campanile
from fastapi import FastAPI

from assay.main import create_app
from assay.openapi_examples import keep_example_nulls


def test_response_examples_keep_their_nulls():
    app = FastAPI()

    @app.get("/thing", responses={200: {"content": {"application/json": {
        "example": {"a": None, "b": 1},
        "examples": {"one": {"summary": "One", "value": {"c": None}}},
    }}}})
    def thing() -> dict:  # pragma: no cover
        return {}

    keep_example_nulls(app)
    content = app.openapi()["paths"]["/thing"]["get"]["responses"]["200"]["content"]

    assert content["application/json"]["example"] == {"a": None, "b": 1}
    assert content["application/json"]["examples"]["one"]["value"] == {"c": None}


def test_the_apps_own_examples_keep_their_nulls():
    responses = create_app().openapi()["paths"]["/statistics/batches/{batch_id}"]["get"][
        "responses"]

    pending = responses["200"]["content"]["application/json"]["examples"]["pending"]["value"]
    assert pending["result"] is None
