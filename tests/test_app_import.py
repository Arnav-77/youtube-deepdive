"""The API module imports cleanly. main.py has no other tests.

Importing app.main pulls in every module the server uses (generation,
retrieval, embeddings, youtube, models). A broken import anywhere in that
chain (a renamed function, a moved module) fails here, offline, instead of
on Render's first request. tests/conftest.py has already set up the offline
environment before this file is imported.
"""

from fastapi import FastAPI


def test_app_main_imports_and_exposes_the_fastapi_app():
    import app.main

    assert isinstance(app.main.app, FastAPI)


def test_app_main_registers_the_public_routes():
    import app.main

    paths = {route.path for route in app.main.app.routes}
    assert {"/health", "/ready", "/videos", "/ask"} <= paths
