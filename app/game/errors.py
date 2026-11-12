class MoveRejected(Exception):
    """The move isn't allowed in the match's current state; the message says why.

    Raised by the crud layer and `app/services/match_play.py`; routes turn it into a
    409 and the computer opponent skips its turn.
    """
