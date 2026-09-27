# Known issues and limitations

The original script's bugs (loose deletion matching, lost unlisted-Vimeo hashes,
missed URL forms, stripped `<iframe>` embeds, endless retries, estimated file sizes,
no lock, no tests) were fixed in 2.0.0. See `CHANGELOG.md`. The items below are
still open. Remove an entry when it is fixed.

| Sev | Issue | Notes |
|-----|-------|-------|
| med | **Error classification depends on English message text** from YouTube, Vimeo, and yt-dlp. If the wording changes upstream, a deletion may be reported as `UNKNOWN`. That is safe, but the deletion goes unnoticed. | Watch the log for repeated "Inconclusive check" lines, and add the new phrase to `ytdlp._RULES` with a test. |
| med | **No cookies or login support.** Members-only, age-gated and private videos can't be downloaded, and they end up given up in `failed_downloads`. | yt-dlp's `--cookies` could be passed through with a new option. |
| med | **YouTube bot checks from home IPs.** Heavy use can trigger "Sign in to confirm you're not a bot". The tool backs off (THROTTLED) but can't get around it. | Lower `--check-limit`, raise `--check-delay`, or use cookies (see above). |
| low | Only YouTube and Vimeo are recognised, and the `platform` column has a CHECK constraint. Adding a platform needs a table-rebuild migration. | |
| low | Downloads are sequential. A large backlog, for example a newly added channel feed, can take a long time. `--max-downloads` caps a run. | Channel RSS feeds only list the latest ~15 videos anyway. |
| low | `status` checks every local file on disk, which can be slow on very large archives on network storage. | |
| low | Options must come *after* the subcommand (`scan --db x`, not `--db x scan`). | Standard argparse subcommand behavior. |
| low | The `fcntl` lock is POSIX-only. On Windows, runs are not locked. | Target platform is a Linux home server. |
| low | The original `metadata_json` is a subset of the metadata. The full metadata is in the `.info.json` next to each video. | |
