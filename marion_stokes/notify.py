"""Optional push notifications via a plain HTTP POST.

The message body is plain text, and the title goes in a ``Title`` header. That
works out of the box with ntfy (https://ntfy.sh or self-hosted) and with any
webhook receiver that accepts a text body.
"""

from __future__ import annotations

import logging
import urllib.request


def send(url: str | None, title: str, body: str, logger: logging.Logger) -> bool:
    if not url:
        return False
    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Title": title.encode("ascii", "replace").decode(),
            "Tags": "film_projector",
            "Content-Type": "text/plain; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except Exception as exc:  # noqa: BLE001 - a failed notification must never break a run
        logger.warning("Notification to %s failed: %s", url, exc)
        return False
