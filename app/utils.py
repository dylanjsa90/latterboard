from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current UTC time as a naive datetime, matching the naive DateTime columns.

    SQLite hands those columns back naive, so comparing them against an aware
    datetime.now(timezone.utc) raises TypeError.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
