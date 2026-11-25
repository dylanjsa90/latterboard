"""The days the word-of-the-day archive covers. Each day's word comes from
`vocab_words.daily_word`, so the archive needs no table."""

from datetime import date, timedelta

# The first archived day: `DAILY_ANCHOR` when the archive launched. Its own constant, so
# moving the anchor later never drops a published day.
ARCHIVE_START = date(2026, 12, 6)


def archive_days(today: date) -> list[date]:
    """Every day from `ARCHIVE_START` through `today` (a Pacific day), oldest first."""
    return [
        ARCHIVE_START + timedelta(days=n)
        for n in range((today - ARCHIVE_START).days + 1)
    ]
