import pytest

from marion_stokes.detect import find_video_urls

YT = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.mark.parametrize("text", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL123",
    "https://www.youtube.com/watch?feature=share&v=dQw4w9WgXcQ",
    '<a href="https://www.youtube.com/watch?feature=share&amp;v=dQw4w9WgXcQ">x</a>',
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?t=10",
    "https://www.youtube.com/embed/dQw4w9WgXcQ?rel=0",
    "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
    "https://www.youtube.com/v/dQw4w9WgXcQ",
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://www.youtube.com/live/dQw4w9WgXcQ",
    "see (https://youtu.be/dQw4w9WgXcQ).",
])
def test_youtube_forms(text):
    refs = find_video_urls(text)
    assert [(r.platform, r.video_id, r.url, r.fetch_url) for r in refs] == [
        ("youtube", "dQw4w9WgXcQ", YT, YT)
    ]


@pytest.mark.parametrize("text", [
    "https://www.youtube.com/watch?v=short",           # not an 11-char id
    "https://www.youtube.com/channel/UCabcdefghijk",   # not a video
    "https://www.youtube.com/playlist?list=PL123",
    "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
    "https://vimeo.com/channels/staffpicks",
])
def test_non_videos_ignored(text):
    assert find_video_urls(text) == []


@pytest.mark.parametrize("text", [
    "https://vimeo.com/123456789",
    "https://www.vimeo.com/123456789",
    "https://vimeo.com/channels/staffpicks/123456789",
    "https://vimeo.com/groups/shortfilms/videos/123456789",
    "https://player.vimeo.com/video/123456789",
    "https://player.vimeo.com/video/123456789?autoplay=1",
])
def test_vimeo_public(text):
    refs = find_video_urls(text)
    assert [(r.platform, r.video_id, r.fetch_url) for r in refs] == [
        ("vimeo", "123456789", "https://vimeo.com/123456789")
    ]


@pytest.mark.parametrize("text", [
    "https://vimeo.com/123456789/abcdef1234",
    "https://player.vimeo.com/video/123456789?h=abcdef1234",
    '<iframe src="https://player.vimeo.com/video/123456789?h=abcdef1234&amp;badge=0">',
])
def test_vimeo_unlisted_keeps_hash(text):
    [ref] = find_video_urls(text)
    assert ref.url == "https://vimeo.com/123456789"
    assert ref.fetch_url == "https://vimeo.com/123456789/abcdef1234"


def test_dedupes_and_prefers_hashed_vimeo():
    text = (
        "https://vimeo.com/123456789 and https://player.vimeo.com/video/123456789?h=abcdef1234 "
        "and https://youtu.be/dQw4w9WgXcQ and https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    )
    refs = find_video_urls(text)
    assert [r.key for r in refs] == [("vimeo", "123456789"), ("youtube", "dQw4w9WgXcQ")]
    assert refs[0].fetch_url.endswith("/abcdef1234")


def test_order_of_appearance():
    text = "https://vimeo.com/111 https://youtu.be/dQw4w9WgXcQ https://vimeo.com/222"
    assert [r.video_id for r in find_video_urls(text)] == ["111", "dQw4w9WgXcQ", "222"]
