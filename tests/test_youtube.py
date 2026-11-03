"""Tests for extract_video_id in app/youtube.py."""

import pytest

from app.youtube import extract_video_id

ID = "LXaFHI_2Hus"


@pytest.mark.parametrize(
    "pasted",
    [
        ID,
        f"  {ID}\n",
        f"https://www.youtube.com/watch?v={ID}",
        f"https://www.youtube.com/watch?v={ID}&t=42",
        f"https://www.youtube.com/watch?list=PLabc&v={ID}",
        f"https://m.youtube.com/watch?v={ID}",
        f"https://youtu.be/{ID}?si=abc123",
        f"https://www.youtube.com/embed/{ID}",
        f"https://www.youtube.com/shorts/{ID}",
    ],
)
def test_accepts_every_form_users_paste(pasted):
    assert extract_video_id(pasted) == ID


@pytest.mark.parametrize(
    "pasted",
    [
        "",
        "hello",
        "LXaFHI_2Hu",        # 10 characters
        "LXaFHI_2Husx",      # 12 characters, bare
        "https://www.youtube.com/",
        "https://vimeo.com/123456789",
    ],
)
def test_rejects_bad_links_with_value_error(pasted):
    with pytest.raises(ValueError):
        extract_video_id(pasted)


@pytest.mark.parametrize(
    "pasted",
    [f"https://youtu.be/{ID}x", f"https://www.youtube.com/watch?v={ID}x"],
)
def test_rejects_too_long_id_inside_url(pasted):
    with pytest.raises(ValueError):
        extract_video_id(pasted)
