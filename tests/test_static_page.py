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


def test_page_shows_five_videos_per_page_with_previous_and_next(page_html):
    assert "const PAGE_SIZE = 5;" in page_html
    assert 'id="prev"' in page_html and ">Previous</button>" in page_html
    assert 'id="next"' in page_html and ">Next</button>" in page_html


def test_paging_uses_the_one_videos_response_and_no_server_paging(page_html):
    # The whole list comes in one request. No page or offset parameter is sent.
    assert page_html.count('fetch("/videos")') == 1
    assert 'fetch("/videos?' not in page_html
    assert "offset=" not in page_html  # a query parameter, not CSS outline-offset
    assert "page=" not in page_html    # likewise: no page number sent to the server


def test_title_is_shown_under_the_thumbnail_with_untitled_as_fallback(page_html):
    assert "video.title" in page_html
    assert "Untitled video" in page_html
    # A card never shows the raw id as its name.
    assert 'el("code"' not in page_html


def test_card_titles_are_at_most_two_lines_with_an_ellipsis(page_html):
    assert "-webkit-line-clamp: 2;" in page_html
    assert "overflow: hidden;" in page_html


def test_thumbnails_load_lazily(page_html):
    assert 'loading: "lazy"' in page_html


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
