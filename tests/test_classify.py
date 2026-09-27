import pytest

from marion_stokes.ytdlp import Verdict, classify_error


@pytest.mark.parametrize("msg,verdict", [
    # Definitive removals
    ("ERROR: [youtube] abc: This video has been removed by the uploader", Verdict.REMOVED),
    ("ERROR: [youtube] abc: This video is no longer available because the YouTube account "
     "associated with this video has been terminated.", Verdict.REMOVED),
    ("ERROR: [youtube] abc: This video has been removed for violating YouTube's Terms of Service",
     Verdict.REMOVED),
    ("ERROR: [youtube] abc: Video unavailable. This video is no longer available due to a "
     "copyright claim by Someone", Verdict.REMOVED),
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video",
     Verdict.PRIVATE),
    # Generic "gone" wording needs confirmation
    ("ERROR: [youtube] abc: Video unavailable", Verdict.GONE_WEAK),
    ("ERROR: [vimeo] 123: Unable to download JSON metadata: HTTP Error 404: Not Found", Verdict.GONE_WEAK),
    # Must NOT count as deleted (false positives in the original code)
    ("ERROR: [youtube] abc: Requested format is not available. Use --list-formats", Verdict.UNKNOWN),
    ("ERROR: [youtube] abc: Video unavailable. The uploader has not made this video available "
     "in your country", Verdict.GEO_BLOCKED),
    ("ERROR: [youtube] abc: This video is not available from your location due to geo restriction",
     Verdict.GEO_BLOCKED),
    ("ERROR: [youtube] abc: Video unavailable. This content isn't available, try again later. "
     "The current session has been rate-limited by YouTube", Verdict.THROTTLED),
    ("ERROR: [youtube] abc: Sign in to confirm you're not a bot.", Verdict.THROTTLED),
    ("ERROR: [youtube] abc: HTTP Error 429: Too Many Requests", Verdict.THROTTLED),
    ("ERROR: [youtube] abc: Join this channel to get access to members-only content", Verdict.RESTRICTED),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.",
     Verdict.RESTRICTED),
    ("ERROR: [youtube] abc: This live event will begin in 3 hours.", Verdict.UPCOMING),
    ("ERROR: [youtube] abc: Premieres in 2 days", Verdict.UPCOMING),
    ("ERROR: [youtube] abc: Unable to download API page: <urlopen error [Errno -3] "
     "Temporary failure in name resolution>", Verdict.UNKNOWN),
    ("", Verdict.UNKNOWN),
])
def test_classify(msg, verdict):
    assert classify_error(msg) is verdict
