"""The web page at /ui/ and the root JSON that points to it.

Offline: TestClient serves the file from disk. These are checks on the page's
text, not a browser test. Running the page for real (asking a question) calls
Gemini, so that is done by hand, not here.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.main import STATIC_DIR, app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def page_html():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


def test_ui_serves_the_page_as_html(client):
    response = client.get("/ui/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>YouTube Deep-Dive</title>" in response.text


def test_page_never_writes_html_from_strings(page_html):
    # Model output is untrusted text. Setting innerHTML would let it run as
    # markup. Matches property access (".innerHTML"), so comments that name
    # the rule do not trip it.
    assert not re.search(r"\.\s*(inner|outer)HTML\b", page_html)
    assert "insertAdjacentHTML" not in page_html
    assert "document.write" not in page_html


def test_page_loads_nothing_from_outside_the_server(page_html):
    # One file: no CDN scripts and no external stylesheets. Only the YouTube
    # thumbnail host is named, and it is loaded by the visitor's browser.
    assert not re.search(r"<script[^>]*\bsrc=", page_html, re.IGNORECASE)
    assert not re.search(r"<link[^>]*stylesheet", page_html, re.IGNORECASE)
    assert "https://img.youtube.com/vi/" in page_html


def test_page_styles_are_plain_css_with_no_imports_or_outside_files(page_html):
    # Plain CSS only: no @import, no url() pointing at another host or font service.
    assert "@import" not in page_html
    assert not re.search(r"url\(\s*['\"]?https?:", page_html)
    assert "fonts.googleapis" not in page_html


def test_page_calls_the_two_api_endpoints_on_its_own_origin(page_html):
    assert 'fetch("/videos")' in page_html
    assert 'fetch("/ask"' in page_html
    assert "http://" not in page_html  # nothing plain-HTTP, nothing off-origin but https thumbnails


def test_selected_video_is_searched_for_on_the_whole_page(page_html):
    # The video radios sit in #videos, outside #ask-form. Searching the form
    # finds nothing, and the page always answers "Pick a video first".
    assert 'document.querySelector(\'input[name="video"]:checked\')' in page_html
    assert "form.querySelector" not in page_html


def test_no_video_is_selected_by_default(page_html):
    # The visitor picks a video; the page does not choose one for them.
    assert "radio.checked" not in page_html


def test_page_gives_the_question_the_server_limit(page_html):
    # Matches AskRequest's max_length, so a long question is stopped in the page.
    assert 'maxlength="500"' in page_html


def test_root_stays_json_and_points_to_the_page(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body == {"service": "deepdive-api", "docs": "/docs", "ui": "/ui/"}


def test_root_answers_head_for_the_render_probe(client):
    response = client.head("/")

    assert response.status_code == 200
