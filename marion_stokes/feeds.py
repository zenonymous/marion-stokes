"""Reading the feed list and extracting video links from RSS/Atom entries."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import feedparser

from .detect import VideoRef, find_video_urls


@dataclass(frozen=True)
class FoundVideo:
    ref: VideoRef
    feed_source: str
    entry_title: str


class FeedError(Exception):
    """A feed could not be fetched or parsed."""


def load_feeds(feeds_file: str) -> list[str]:
    path = Path(feeds_file)
    if not path.exists():
        raise FileNotFoundError(f"Feeds file not found: {feeds_file}")
    feeds = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            feeds.append(line)
    return feeds


def entry_text(entry) -> str:
    """Join every field of a feedparser entry that may contain a video URL."""
    blobs = [
        entry.get("link", ""),
        entry.get("title", ""),
        entry.get("summary", ""),
        entry.get("description", ""),
    ]
    for content_block in entry.get("content", []):
        blobs.append(content_block.get("value", ""))
    for link_obj in entry.get("links", []):
        blobs.append(link_obj.get("href", ""))
    for mc in entry.get("media_content", []):
        blobs.append(mc.get("url", ""))
    player = entry.get("media_player")
    if player:
        blobs.append(player.get("url", "") or "")
        blobs.append(player.get("content", "") or "")
    return "\n".join(b for b in blobs if isinstance(b, str))


def scan_feed(feed_url: str, logger: logging.Logger) -> list[FoundVideo]:
    """Parse one RSS/Atom feed (URL or local path) and return the videos it links to."""
    logger.info("Parsing feed: %s", feed_url)
    # sanitize_html=False: feedparser's sanitizer strips <iframe> embeds, which is
    # exactly where blogs put YouTube/Vimeo players. The HTML is only regex-scanned,
    # never rendered, so skipping sanitisation is safe.
    feed = feedparser.parse(feed_url, sanitize_html=False)

    if feed.bozo and not feed.entries:
        raise FeedError(f"failed to parse feed {feed_url}: {feed.get('bozo_exception')}")
    status = feed.get("status")
    if status and status >= 400:
        raise FeedError(f"feed {feed_url} returned HTTP {status}")

    videos: list[FoundVideo] = []
    for entry in feed.entries:
        title = entry.get("title", "")
        for ref in find_video_urls(entry_text(entry)):
            videos.append(FoundVideo(ref, feed_url, title))

    logger.info("  Found %d video link(s) in feed", len(videos))
    return videos
