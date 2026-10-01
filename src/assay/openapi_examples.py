"""Response examples in the OpenAPI document exactly as written, nulls included.

FastAPI encodes the whole document with `exclude_none=True`, which also strips
every `null` out of the examples we write: a field that is null in a real
response then looks absent in Swagger, and a client built from the examples
learns the wrong shape. After FastAPI generates the document, each route's
response examples are put back as the route declares them.
"""
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRoute


def keep_example_nulls(app: FastAPI) -> None:
    generate = app.openapi

    def openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = generate()
        for route in app.routes:
            if not isinstance(route, APIRoute):
                continue
            operations = schema["paths"].get(route.path_format, {})
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
