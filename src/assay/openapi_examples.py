"""Response examples in the OpenAPI document exactly as written, nulls included.

FastAPI encodes the whole document with `exclude_none=True`, which also strips
every `null` out of the examples we write: a field that is null in a real
response then looks absent in Swagger, and a client built from the examples
learns the wrong shape. After FastAPI generates the document, each route's
response examples are put back as the route declares them.
"""
from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRoute


def api_routes(routes, prefix: str = "") -> Iterator[tuple[APIRoute, str]]:
    """Every API route with the prefix it is served under. An included router
    is either flattened into its parent's routes (FastAPI up to 0.136) or kept
    as one nested entry carrying the router and its include prefix (0.137 on);
    both layouts are walked.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            yield route, prefix
            continue
        included = getattr(route, "original_router", None)
        if included is not None:
            context = getattr(route, "include_context", None)
            yield from api_routes(included.routes, prefix + getattr(context, "prefix", ""))


def keep_example_nulls(app: FastAPI) -> None:
    generate = app.openapi

    def openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = generate()
        for route, prefix in api_routes(app.routes):
            operations = schema["paths"].get(prefix + route.path_format, {})
            for method in route.methods:
                operation = operations.get(method.lower())
                if operation is None:
                    continue
                for code, response in route.responses.items():
                    documented = operation["responses"].get(str(code), {})
                    for media, body in (response or {}).get("content", {}).items():
                        target = documented.get("content", {}).get(media)
                        if target is None:
                            continue
                        if "example" in body:
                            target["example"] = jsonable_encoder(body["example"])
                        for name, example in body.get("examples", {}).items():
                            if "value" in example and name in target.get("examples", {}):
                                target["examples"][name]["value"] = jsonable_encoder(
                                    example["value"])
        return schema

    app.openapi = openapi
