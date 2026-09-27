"""Find YouTube / Vimeo video links in arbitrary text and normalise them."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

# YouTube video IDs are always 11 characters from this alphabet.
_YT_ID = r"[A-Za-z0-9_-]{11}(?![A-Za-z0-9_-])"
_YT_HOST = r"(?:www\.|m\.|music\.)?youtube(?:-nocookie)?\.com"
# Unlisted Vimeo videos need their privacy hash (10 hex chars in practice).
_VIMEO_HASH = r"[0-9a-f]{6,}"

_YT_WATCH = re.compile(rf"https?://{_YT_HOST}/watch\?[^\s\"'<>)]+", re.I)
_YT_PATH = re.compile(rf"https?://{_YT_HOST}/(?:embed|v|shorts|live)/({_YT_ID})", re.I)
_YT_SHORT = re.compile(rf"https?://youtu\.be/({_YT_ID})", re.I)
_VIMEO_PAGE = re.compile(
    r"https?://(?:www\.)?vimeo\.com/"
    r"(?:channels/[\w-]+/|groups/[\w-]+/videos/|album/\d+/video/|showcase/\d+/video/)?"
    rf"(\d+)(?:/({_VIMEO_HASH}))?(?![\w-])",
    re.I,
)
_VIMEO_PLAYER = re.compile(r"https?://player\.vimeo\.com/video/(\d+)([^\s\"'<>)]*)", re.I)


@dataclass(frozen=True)
class VideoRef:
    """One video found in text.

    ``url`` is the canonical identity URL stored in the ``videos.url`` column.
    ``fetch_url`` is what yt-dlp is given; it differs only for unlisted Vimeo
    videos, which need the privacy hash.
    """

    platform: str
    video_id: str
    url: str
    fetch_url: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.platform, self.video_id)


def canonical_url(platform: str, video_id: str) -> str:
    if platform == "youtube":
        return f"https://www.youtube.com/watch?v={video_id}"
    if platform == "vimeo":
        return f"https://vimeo.com/{video_id}"
    raise ValueError(f"unknown platform: {platform}")


def _ref(platform: str, video_id: str, vimeo_hash: str | None = None) -> VideoRef:
    url = canonical_url(platform, video_id)
    fetch = f"{url}/{vimeo_hash}" if vimeo_hash else url
    return VideoRef(platform, video_id, url, fetch)


def _youtube_watch_id(url: str) -> str | None:
    vid = parse_qs(urlparse(url).query).get("v", [""])[0]
    return vid if re.fullmatch(_YT_ID, vid) else None


def find_video_urls(text: str) -> list[VideoRef]:
    """Return every distinct video referenced in ``text``, in order of first appearance.

    HTML entities are decoded first, so ``watch?feature=x&amp;v=ID`` inside
    feed HTML is handled. When the same Vimeo video appears both with and
    without its privacy hash, the hashed fetch URL wins.
    """
    text = html.unescape(text)
    found: list[tuple[int, VideoRef]] = []

    for m in _YT_WATCH.finditer(text):
        if vid := _youtube_watch_id(m.group(0)):
            found.append((m.start(), _ref("youtube", vid)))
    for pat in (_YT_PATH, _YT_SHORT):
        for m in pat.finditer(text):
            found.append((m.start(), _ref("youtube", m.group(1))))
    for m in _VIMEO_PAGE.finditer(text):
        found.append((m.start(), _ref("vimeo", m.group(1), m.group(2))))
    for m in _VIMEO_PLAYER.finditer(text):
        h = parse_qs(urlparse(m.group(2)).query).get("h", [None])[0]
        if h and not re.fullmatch(_VIMEO_HASH, h, re.I):
            h = None
        found.append((m.start(), _ref("vimeo", m.group(1), h)))

    by_key: dict[tuple[str, str], VideoRef] = {}
    for _, ref in sorted(found, key=lambda pair: pair[0]):
        prev = by_key.get(ref.key)
        if prev is None or (prev.fetch_url == prev.url and ref.fetch_url != ref.url):
            by_key[ref.key] = ref
    return list(by_key.values())
