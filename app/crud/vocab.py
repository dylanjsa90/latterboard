"""Vocab Challenger rooms and wordbooks. The room rules are in app/game/vocab.py; this
module loads a room, runs a rule against it, and saves it without losing a concurrent write.
"""

import copy
import logging
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.game import vocab
from app.game.vocab import VocabError
from app.game.vocab_words import WORDS
from app.models.vocab import VocabRoom, VocabSavedWord
from app.schemas.vocab import RoomSnapshot, SavedWord, Wordbook
from app.utils import utcnow

# Every request saves the room (to record `seen`), and both players refetch together
# when it changes, so losing a race is routine; the Worker this replaced allowed 8 too.
MAX_APPLY_ATTEMPTS = 8
CODE_ATTEMPTS = 5
# Expired rooms answer 410 for a week, so a late link still says why, then are deleted.
EXPIRED_ROOM_KEPT = timedelta(days=7)

logger = logging.getLogger(__name__)


def _epoch_ms(value: datetime) -> int:
    """A naive-UTC DB datetime as epoch milliseconds."""
    return int(value.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _snapshot(g: vocab.Game, revision: int, player_id: str, now: int) -> RoomSnapshot:
    view = vocab.visible(g, player_id)
    return RoomSnapshot.model_validate(
        {"game": {**view, "revision": revision}, "server_now": now}
    )


class CRUDVocab:
    # --- rooms ------------------------------------------------------------

    def create_room(
        self, db: Session, mode: vocab.Mode, player_id: str, name: str
    ) -> RoomSnapshot:
        now = vocab.now_ms()
        for _ in range(CODE_ATTEMPTS):
            # Retry only invite-code collisions, never overwrite an existing room.
            code = secrets.token_hex(3).upper()
            g = vocab.make_game(code, mode, vocab.new_player(player_id, name, now), now)
            row = VocabRoom(code=code, state=cast(dict[str, Any], g))
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                continue
            snapshot = _snapshot(g, row.version, player_id, now)
            self._delete_old_rooms(db)
            return snapshot
        raise VocabError("Could not create an invite. Please try again.", 503)

    def _delete_old_rooms(self, db: Session) -> None:
        """Delete rooms a week past expiry. Runs as part of creating a room, since
        latterboard has no timers, and never fails the new room (it's already saved)."""
        lifetime = timedelta(milliseconds=vocab.ROOM_LIFETIME_MS)
        cutoff = utcnow() - lifetime - EXPIRED_ROOM_KEPT
        try:
            db.query(VocabRoom).filter(VocabRoom.created_at < cutoff).delete(
                synchronize_session=False
            )
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            logger.exception("Could not delete old vocab rooms")

    def mutate(
        self,
        db: Session,
        code: str,
        player_id: str,
        change: Callable[[vocab.Game, int], None],
    ) -> tuple[RoomSnapshot, bool]:
        """Run `change(game, now)` against the room and save it, returning the
        player's view and whether the room changed beyond `seen` (see vocab.changed).

        If another request saves the room first, VocabRoom.version turns our write
        into a StaleDataError and we replay `change` on the fresh state instead of
        overwriting theirs. A VocabError from `change` saves nothing.
        """
        for _ in range(MAX_APPLY_ATTEMPTS):
            row = db.get(VocabRoom, code)
            if row is None:
                raise VocabError("Room not found. Check the six-character code.", 404)
            now = vocab.now_ms()
            # Change a copy and reassign: JSON columns don't track in-place changes.
            g = cast(vocab.Game, copy.deepcopy(row.state))
            if vocab.expired(g, now):
                raise VocabError("This room has expired. Create a new challenge.", 410)
            change(g, now)
            news = vocab.changed(cast(vocab.Game, row.state), g)
            row.state = cast(dict[str, Any], g)
            try:
                db.commit()
            except StaleDataError:
                db.rollback()
                continue
            return _snapshot(g, row.version, player_id, now), news
        raise VocabError("The room is busy. Please try again.", 409)

    def is_participant(self, db: Session, code: str, player_id: str) -> bool:
        row = db.get(VocabRoom, code)
        return row is not None and any(
            p["id"] == player_id for p in row.state["players"]
        )

    # --- wordbook ---------------------------------------------------------

    def wordbook(self, db: Session, user_id: int) -> Wordbook:
        rows = (
            db.query(VocabSavedWord)
            .filter(VocabSavedWord.user_id == user_id)
            .order_by(VocabSavedWord.due)
            .all()
        )
        saved = []
        for row in rows:
            if row.word_id >= len(WORDS):
                continue
            w = WORDS[row.word_id]
            saved.append(
                SavedWord(
                    id=w.id,
                    word=w.word,
                    pos=w.pos,
                    definition=w.definition,
                    difficulty=w.difficulty,
                    example=w.example,
                    synonyms=w.synonyms,
                    nuance=w.nuance,
                    due=_epoch_ms(row.due),
                    level=row.level,
                )
            )
        return Wordbook(saved=saved, word_count=len(WORDS))

    def _get_saved(
        self, db: Session, user_id: int, word_id: int
    ) -> VocabSavedWord | None:
        return (
            db.query(VocabSavedWord)
            .filter(
                VocabSavedWord.user_id == user_id, VocabSavedWord.word_id == word_id
            )
            .one_or_none()
        )

    def save(self, db: Session, user_id: int, word_id: int) -> None:
        """Add the word to the wordbook, due now; saving it again changes nothing."""
        if self._get_saved(db, user_id, word_id) is not None:
            return
        db.add(VocabSavedWord(user_id=user_id, word_id=word_id, level=0, due=utcnow()))
        try:
            db.commit()
        except IntegrityError:
            # A concurrent save of the same word won.
            db.rollback()

    def remove(self, db: Session, user_id: int, word_id: int) -> None:
        db.query(VocabSavedWord).filter(
            VocabSavedWord.user_id == user_id, VocabSavedWord.word_id == word_id
        ).delete(synchronize_session=False)
        db.commit()

    def review(self, db: Session, user_id: int, word_id: int, known: bool) -> None:
        row = self._get_saved(db, user_id, word_id)
        if row is None:
            raise VocabError("That word is no longer saved.", 404)
        row.level, wait = vocab.next_review(row.level, known)
        row.due = utcnow() + wait
        db.commit()


crud_vocab = CRUDVocab()
