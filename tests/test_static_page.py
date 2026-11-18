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


def test_title_is_shown_under_the_thumbnail_with_the_video_id_as_fallback(page_html):
    assert "video.title" in page_html
    # No title: "Video <id>", so two untitled videos can be told apart.
    assert "`Video ${id}`" in page_html
    assert "Untitled video" not in page_html
    # The id is a text node in a span, never markup.
    assert 'el("code"' not in page_html


def test_card_titles_are_at_most_two_lines_with_an_ellipsis(page_html):
    assert "-webkit-line-clamp: 2;" in page_html
    assert "overflow: hidden;" in page_html


def test_answer_shows_only_bold_bullets_and_line_breaks(page_html):
    # The page builds these elements and nothing else for the answer.
    assert 'el("strong", {}' in page_html          # **bold**
    assert 'el("ul")' in page_html                 # bullet list
    assert 'el("li", {}' in page_html              # each bullet
    assert 'el("br")' in page_html                 # line break inside a paragraph
    assert 'el("p")' in page_html                  # paragraph
    # The two patterns: **bold**, and "* " or "- " at the start of a line.
    assert r"/\*\*(.+?)\*\*/g" in page_html
    assert r"/^\s*[*-]\s+(.*)$/" in page_html


def test_citations_still_work_inside_bold_and_list_items(page_html):
    assert "citationNodes(match[1], byNumber)" in page_html   # inside **bold**
    assert "inlineNodes(bullet[1], byNumber)" in page_html    # inside a bullet
    assert "inlineNodes(line, byNumber)" in page_html         # inside a paragraph


def test_model_text_never_sets_a_link_target_or_markup(page_html):
    # Links come only from the server's citation URLs, never from answer text.
    assert "href: citation.url" in page_html
    assert "renderAnswer" not in page_html  # the old, text-only renderer is gone


def test_viewport_meta_tag_is_present(page_html):
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page_html


def test_question_comes_before_the_videos_on_narrow_screens(page_html):
    # Narrow screens stack the panes in this order: the question first.
    assert 'grid-template-areas: "ask" "videos";' in page_html
    assert page_html.index('id="ask-heading"') < page_html.index('id="videos-heading"')


def test_wide_screens_put_the_picker_left_and_the_question_right(page_html):
    assert "@media (min-width: 901px)" in page_html
    assert 'grid-template-areas: "videos ask";' in page_html
    assert "repeat(2, minmax(0, 1fr))" in page_html


def test_each_pane_scrolls_inside_itself_on_wide_screens(page_html):
    wide = page_html[page_html.index("@media (min-width: 901px)"):]
    assert "height: 100dvh;" in wide
    assert "overflow-y: auto;" in wide  # on .pane
    assert "min-height: 0;" in wide


def test_enter_asks_and_shift_enter_is_left_for_a_new_line(page_html):
    assert 'addEventListener("keydown"' in page_html
    assert 'event.key !== "Enter" || event.shiftKey' in page_html
    assert "requestSubmit()" in page_html


def test_enter_is_ignored_while_an_input_method_is_composing(page_html):
    assert "event.isComposing" in page_html


def test_enter_cannot_send_a_second_question_while_one_is_being_answered(page_html):
    assert "if (button.disabled) return;" in page_html


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


def test_a_row_is_labelled_not_checked_unless_checked_is_true(page_html):
    # Anything but an explicit true gets the label, so a missing flag is not
    # shown as checked.
    row_builder = page_html[page_html.index("function videoRow(video)"):]
    row_builder = row_builder[: row_builder.index("\n}\n") + 3]
    assert "video.checked !== true" in row_builder
    assert '"not checked"' in row_builder


def test_the_not_checked_label_is_built_as_a_text_node(page_html):
    # Through el(), whose string children become text nodes: never markup.
    assert 'el("span", { className: "unchecked" }, "not checked")' in page_html


def test_the_not_checked_label_is_styled_in_plain_css(page_html):
    assert ".unchecked {" in page_html


# --- polish: tab icon, times, waiting state, missing titles, add a video ------


def _function_source(page_html, signature):
    """The text of one top-level function in the page's script."""
    start = page_html.index(signature)
    return page_html[start: page_html.index("\n}\n", start) + 3]


def test_the_tab_icon_is_inline_so_the_browser_never_asks_for_favicon_ico(page_html):
    head = page_html[: page_html.index("</head>")]
    assert '<link rel="icon" href="data:,">' in head
    assert "favicon.ico" not in page_html.replace("<!-- An empty inline icon: the browser does not ask the server for /favicon.ico. -->", "")


def test_times_use_hours_from_one_hour_up_and_minutes_below(page_html):
    clock = _function_source(page_html, "function clock(seconds)")
    assert "Math.floor(total / 3600)" in clock
    assert "if (hours > 0)" in clock
    # h:mm:ss with two-digit minutes and seconds; m:ss otherwise.
    assert 'return `${hours}:${String(minutes).padStart(2, "0")}:${secs}`;' in clock
    assert "return `${minutes}:${secs}`;" in clock
    assert "mmss" not in page_html


def test_citation_links_are_built_from_seconds_and_only_the_label_uses_the_clock(page_html):
    link = _function_source(page_html, "function citationLink(citation)")
    assert "href: citation.url" in link
    assert "clock(citation.start_seconds)" in link
    assert "start_seconds" not in link.replace("clock(citation.start_seconds)", "")


def test_asking_disables_the_button_and_says_answering_with_a_seconds_counter(page_html):
    ask = _function_source(page_html, "async function ask(event)")
    assert "button.disabled = true;" in ask
    assert 'button.textContent = "Answering...";' in ask
    assert '"Answering... "' in ask
    assert "setInterval(" in ask and "Math.floor((Date.now() - started) / 1000)" in ask
    assert "} finally {" in ask
    # The timer is always stopped and the button always comes back.
    finally_part = ask[ask.index("} finally {"):]
    assert "clearInterval(timer);" in finally_part
    assert "button.disabled = false;" in finally_part
    assert 'button.textContent = "Ask";' in finally_part


def test_after_15_seconds_the_page_says_the_main_model_is_busy(page_html):
    assert "const SLOW_AFTER_S = 15;" in page_html
    assert ('const SLOW_NOTE = "The main model is busy. Switching to the backup can take up to a minute.";'
            in page_html)
    assert "secs >= SLOW_AFTER_S" in page_html
    assert 'id="slow-note"' in page_html


def test_an_error_while_asking_shows_a_plain_message_and_the_button_is_enabled_again(page_html):
    ask = _function_source(page_html, "async function ask(event)")
    catch_part = ask[ask.index("} catch (err) {"): ask.index("} finally {")]
    assert 'status.className = "error";' in catch_part
    assert "status.textContent = err.message;" in catch_part
    assert "Could not reach the server" in ask


def test_a_video_without_a_title_is_named_video_and_its_id(page_html):
    name = _function_source(page_html, "function nameOf(id)")
    assert "`Video ${id}`" in name
    assert "if (video && video.title)" in name


def test_the_add_panel_has_a_link_field_a_transcript_box_and_an_add_button(page_html):
    assert '<form id="add-form"' in page_html
    assert 'id="add-url"' in page_html and "YouTube link" in page_html
    assert '<textarea id="add-transcript"' in page_html
    assert '<button type="submit" id="add-button">Add video</button>' in page_html
    assert 'document.getElementById("add-form").addEventListener("submit", addVideo);' in page_html


def test_the_add_panel_gives_three_lines_on_copying_a_transcript_from_youtube(page_html):
    help_block = page_html[page_html.index('<ol class="help">'): page_html.index("</ol>", page_html.index('<ol class="help">'))]
    assert help_block.count("<li>") == 3
    assert "...more" in help_block and "Show transcript" in help_block
    assert "Drag-select" in help_block
    assert "Ctrl+C" in help_block


def test_adding_posts_to_the_paste_route_as_a_visitor_with_no_token(page_html):
    add = _function_source(page_html, "async function addVideo(event)")
    assert 'fetch("/videos/paste"' in add
    assert 'method: "POST"' in add
    assert "JSON.stringify({ url:" in add
    assert "X-Paste-Token" not in page_html
    assert "x-paste-token" not in page_html.lower()
    assert "PASTE_TOKEN" not in page_html


def test_each_paste_refusal_is_explained_in_plain_words(page_html):
    failure = _function_source(page_html, "function addFailure(status, data)")
    for code in ("409", "422", "429"):
        assert f"status === {code}" in failure
    assert "cannot be added again" in failure
    assert "did not accept that transcript" in failure
    assert "limit for added videos" in failure
    # The server's reason comes after the plain sentence, from either field it uses.
    assert "data.message" in page_html and "data.detail" in page_html


def test_a_202_is_polled_until_ready_and_then_the_list_is_refreshed_and_the_video_selected(page_html):
    add = _function_source(page_html, "async function addVideo(event)")
    assert "response.status !== 200 && response.status !== 202" in add
    assert "await waitUntilReady(data.video_id, data, show)" in add
    assert "await loadVideos(ready.video_id);" in add
    wait = _function_source(page_html, "async function waitUntilReady(videoId, first, show)")
    assert "`/videos/${encodeURIComponent(videoId)}`" in wait
    assert 'while (state.status !== "ready")' in wait
    assert 'state.status === "incomplete"' in wait   # stopped job: say so, do not poll forever
    assert "ADD_POLL_FOR_MS" in wait                  # and never wait forever
    select = _function_source(page_html, "function selectVideo(id)")
    assert "checked = true" in select and "showChosen(id)" in select


def test_the_add_button_is_disabled_while_sending_and_enabled_again_after(page_html):
    add = _function_source(page_html, "async function addVideo(event)")
    assert "if (button.disabled) return;" in add
    assert 'button.textContent = "Adding...";' in add
    finally_part = add[add.index("} finally {"):]
    assert "button.disabled = false;" in finally_part
    assert 'button.textContent = "Add video";' in finally_part


def test_the_add_panel_only_writes_text_never_markup(page_html):
    script = page_html[page_html.index("// --- Add a video"):]
    assert ".textContent = text;" in script
    assert not re.search(r"\.\s*(inner|outer)HTML\b", script)
    assert "insertAdjacentHTML" not in script
    # The server's words reach the page only through textContent.
    assert "show(err.message" in script


def test_the_list_can_be_reloaded_without_a_second_kind_of_request(page_html):
    # One fetch of /videos, reused for the first load and the refresh after an add.
    assert page_html.count('fetch("/videos")') == 1
    assert "async function loadVideos(selectId)" in page_html
    assert "videosById.clear();" in page_html and "list.replaceChildren();" in page_html
