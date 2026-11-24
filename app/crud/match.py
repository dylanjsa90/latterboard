from datetime import timedelta

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Query, Session

from app.crud.base import CRUDBase
from app.crud.game_score import game_score as crud_game_score
from app.crud.match_puzzle import match_puzzle as crud_match_puzzle
from app.crud.user import user as crud_user
from app.game import match_modes, wordle
from app.models.match import Match, MatchGuess
from app.models.user import User
from app.schemas.game_score import GameScoreCreate
from app.schemas.match import MatchInviteCreate, MatchRecord
from app.utils import utcnow

OPEN_STATUSES = ("pending_invite", "in_progress")


class CRUDMatch(CRUDBase[Match, MatchInviteCreate, MatchInviteCreate]):
    def create_invite(
        self,
        db: Session,
        inviter_id: int,
        invitee_id: int,
        game: str,
        max_guesses: int,
        expiry_minutes: int,
    ) -> Match:
        match = Match(
            inviter_id=inviter_id,
            invitee_id=invitee_id,
            game=game,
            status="pending_invite",
            # Race and co-op matches keep their puzzle in MatchPuzzle instead; the
            # column is non-null, hence the empty string.
            target_word=wordle.select_word() if game.startswith("word") else "",
            max_guesses=max_guesses,
            invite_expires_at=utcnow() + timedelta(minutes=expiry_minutes),
        )
        db.add(match)
        if game in match_modes.PUZZLE_MATCH_GAMES:
            db.flush()  # assigns match.id for the puzzle row
            db.add(crud_match_puzzle.build(match))
        db.commit()
        db.refresh(match)
        return match

    def expire_if_needed(self, db: Session, match: Match) -> Match:
        if (
            match.status == "pending_invite"
            and match.invite_expires_at is not None
            and match.invite_expires_at < utcnow()
        ):
            match.status = "expired"
            db.add(match)
            db.commit()
            db.refresh(match)
        return match

    def get_open_match_between(
        self, db: Session, user_a_id: int, user_b_id: int, game: str
    ) -> Match | None:
        match = (
            db.query(Match)
            .filter(
                Match.game == game,
                Match.status.in_(OPEN_STATUSES),
                or_(
                    and_(Match.inviter_id == user_a_id, Match.invitee_id == user_b_id),
                    and_(Match.inviter_id == user_b_id, Match.invitee_id == user_a_id),
                ),
            )
            .first()
        )
        if match is None:
            return None
        match = self.expire_if_needed(db, match)
        return match if match.status in OPEN_STATUSES else None

    def get_pending_invites(self, db: Session, user_id: int) -> list[Match]:
        matches = (
            db.query(Match)
            .filter(Match.invitee_id == user_id, Match.status == "pending_invite")
            .order_by(Match.created_at.desc())
            .all()
        )
        fresh = [self.expire_if_needed(db, m) for m in matches]
        return [m for m in fresh if m.status == "pending_invite"]

    def get_finished(
        self, db: Session, user_id: int, *, skip: int, limit: int
    ) -> tuple[list[Match], int]:
        """A page of the player's completed matches, newest first, and how many there are."""
        finished = self._finished(db, user_id)
        rows = (
            # id breaks ties so pages stay stable when two matches share a timestamp.
            finished.order_by(Match.completed_at.desc(), Match.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return rows, finished.count()

    def get_record(self, db: Session, user_id: int) -> MatchRecord:
        # COUNT skips the NULLs a CASE without ELSE yields, so each counts its own rows.
        # Matches against the computer are left out: a solo game shouldn't pad, or
        # dent, the record a player is judged on. They still appear in the history.
        bot_ids = crud_user.bot_ids(db)
        won, lost, drawn = (
            self._finished(db, user_id)
            # Co-op is won or lost together, so it has no winner to count.
            .filter(Match.game != match_modes.SUDOKU_COOP)
            .filter(~Match.inviter_id.in_(bot_ids), ~Match.invitee_id.in_(bot_ids))
            .with_entities(
                func.count(case((Match.winner_id == user_id, 1))),
                func.count(
                    case(
                        (
                            and_(
                                Match.winner_id.isnot(None), Match.winner_id != user_id
                            ),
                            1,
                        )
                    )
                ),
                func.count(case((Match.winner_id.is_(None), 1))),
            )
            .one()
        )
        return MatchRecord(won=won, lost=lost, drawn=drawn)

    def _finished(self, db: Session, user_id: int) -> Query[Match]:
        return db.query(Match).filter(
            or_(Match.inviter_id == user_id, Match.invitee_id == user_id),
            Match.status == "completed",
        )

    def accept_invite(self, db: Session, match: Match) -> Match:
        match.status = "in_progress"
        match.started_at = utcnow()
        # Races and co-op have no turns: both players move whenever they like.
        match.current_turn_user_id = (
            match.inviter_id if match.game == "wordle" else None
        )
        db.add(match)
        db.commit()
        db.refresh(match)
        return match

    def decline_invite(self, db: Session, match: Match) -> Match:
        match.status = "declined"
        db.add(match)
        db.commit()
        db.refresh(match)
        return match

    def cancel_invite(self, db: Session, match: Match) -> Match:
        match.status = "cancelled"
        db.add(match)
        db.commit()
        db.refresh(match)
        return match

    def record_guess(
        self, db: Session, match: Match, user: User, word: str
    ) -> MatchGuess:
        """Save a turn-based guess and move the match on: the next turn, or its end."""
        turn_number = (
            db.query(func.count(MatchGuess.id))
            .filter(MatchGuess.match_id == match.id)
            .scalar()
            or 0
        ) + 1
        result_values = wordle.evaluate_guess(match.target_word, word)
        is_correct = all(status == "correct" for status in result_values)

        guess = MatchGuess(
            match_id=match.id,
            user_id=user.id,
            turn_number=turn_number,
            word=word.lower(),
            result=result_values,
            is_correct=is_correct,
        )
        db.add(guess)

        self._advance(match, user.id, is_correct, turn_number)
        db.add(match)
        db.commit()
        db.refresh(match)

        if match.status == "completed":
            self._record_scores(db, match)
        return guess

    def _advance(
        self, match: Match, user_id: int, solved: bool, turn_number: int
    ) -> None:
        """End the match on a solve or its last guess; otherwise hand the turn over."""
        if solved or turn_number >= match.max_guesses:
            match.status = "completed"
            match.winner_id = user_id if solved else None
            match.completed_at = utcnow()
            match.current_turn_user_id = None
        else:
            match.current_turn_user_id = (
                match.invitee_id if user_id == match.inviter_id else match.inviter_id
            )

    def _record_scores(self, db: Session, match: Match) -> None:
        if crud_user.any_are_bots(db, match.inviter_id, match.invitee_id):
            # Both players are written a row here, so without this the computer
            # would climb the leaderboard — and so would anyone farming it. These
            # writes bypass `POST /scores/`, so its daily cap wouldn't catch it.
            return
        per_player_max = max(match.max_guesses // 2, 1)
        if match.winner_id is not None:
            winner_guess_count = (
                db.query(func.count(MatchGuess.id))
                .filter(
                    MatchGuess.match_id == match.id,
                    MatchGuess.user_id == match.winner_id,
                )
                .scalar()
                or 0
            )
            winner_score = 100 * max(per_player_max - winner_guess_count + 1, 1)
            loser_id = (
                match.invitee_id
                if match.winner_id == match.inviter_id
                else match.inviter_id
            )
            crud_game_score.create_score(
                db,
                match.winner_id,
                GameScoreCreate(game="wordle_vs", score=winner_score),
            )
            crud_game_score.create_score(
                db, loser_id, GameScoreCreate(game="wordle_vs", score=0)
            )
        else:
            crud_game_score.create_score(
                db, match.inviter_id, GameScoreCreate(game="wordle_vs", score=0)
            )
            crud_game_score.create_score(
                db, match.invitee_id, GameScoreCreate(game="wordle_vs", score=0)
            )

    def get_guesses(self, db: Session, match_id: int) -> list[MatchGuess]:
        return (
            db.query(MatchGuess)
            .filter(MatchGuess.match_id == match_id)
            .order_by(MatchGuess.turn_number)
            .all()
        )

    def get_active(self, db: Session, user_id: int) -> list[Match]:
        """The player's pending and in-progress matches, newest first."""
        rows = (
            db.query(Match)
            .filter(
                or_(Match.inviter_id == user_id, Match.invitee_id == user_id),
                Match.status.in_(OPEN_STATUSES),
            )
            .order_by(Match.created_at.desc(), Match.id.desc())
            .all()
        )
        fresh = [self.expire_if_needed(db, m) for m in rows]
        return [m for m in fresh if m.status in OPEN_STATUSES]


match = CRUDMatch(Match)
