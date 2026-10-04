"""Every success example any endpoint shows in Swagger is a valid response of
that endpoint: an example that drifted from the schema would teach the FE the
wrong shape."""
import pytest
from pydantic import TypeAdapter

from assay.main import create_app
from assay.openapi_examples import api_routes


def _examples(content: dict) -> list:
    if "example" in content:
        return [content["example"]]
    return [example["value"] for example in content.get("examples", {}).values()]


def _documented() -> list[tuple[str, str, object]]:
    """(method and path, status, example) for every success example of a route
    with a response model."""
    found = []
    for route, prefix in api_routes(create_app().routes):
        if route.response_model is None:
            continue
        success = {str(route.status_code or 200)}
        for status, response in (route.responses or {}).items():
            if str(status) not in success:
                continue
            content = response.get("content", {}).get("application/json", {})
            for example in _examples(content):
                for method in route.methods:
                    found.append((f"{method} {prefix}{route.path}", str(status), example,
                                  route.response_model))
    return found


_EXAMPLES = _documented()


def test_there_are_examples_to_check():
    assert len(_EXAMPLES) > 40


@pytest.mark.parametrize(("route", "status", "example", "model"), _EXAMPLES,
                         ids=[f"{route} {status} #{n}" for n, (route, status, _, _)
                              in enumerate(_EXAMPLES)])
def test_every_success_example_is_a_valid_response(route, status, example, model):
    TypeAdapter(model).validate_python(example)
