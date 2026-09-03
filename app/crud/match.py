from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.crud.game_score import game_score as crud_game_score
from app.game import wordle
from app.models.match import Match, MatchGuess
from app.models.user import User
from app.schemas.game_score import GameScoreCreate
from app.schemas.match import (
    LetterResultOut,
    MatchDetail,
    MatchGuessResult,
    MatchInviteCreate,
    MatchPublic,
    PendingInvite,
)

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
            target_word=wordle.select_word(),
            max_guesses=max_guesses,
            invite_expires_at=datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes),
        )
        db.add(match)
        db.commit()
        db.refresh(match)
        return match

    def expire_if_needed(self, db: Session, match: Match) -> Match:
        if (
            match.status == "pending_invite"
            and match.invite_expires_at is not None
            and match.invite_expires_at < datetime.now(timezone.utc)
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

    def accept_invite(self, db: Session, match: Match) -> Match:
        match.status = "in_progress"
        match.started_at = datetime.utcnow()
        match.current_turn_user_id = match.inviter_id
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

    def submit_guess(
        self, db: Session, match: Match, user: User, word: str
    ) -> MatchGuessResult:
        turn_number = (
            db.query(func.count(MatchGuess.id))
            .filter(MatchGuess.match_id == match.id)
            .scalar()
            or 0
        ) + 1
        letter_results = wordle.evaluate_guess(match.target_word, word)
        result_values = [status for _, status in letter_results]
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

        if is_correct:
            match.status = "completed"
            match.winner_id = user.id
            match.completed_at = datetime.utcnow()
            match.current_turn_user_id = None
        elif turn_number >= match.max_guesses:
            match.status = "completed"
            match.winner_id = None
            match.completed_at = datetime.utcnow()
            match.current_turn_user_id = None
        else:
            match.current_turn_user_id = (
                match.invitee_id if user.id == match.inviter_id else match.inviter_id
            )

        db.add(match)
        db.commit()
        db.refresh(match)

        if match.status == "completed":
            self._record_scores(db, match)

        current_turn_username = self._username_or_none(db, match.current_turn_user_id)
        winner_username = self._username_or_none(db, match.winner_id)

        return MatchGuessResult(
            match_id=match.id,
            turn_number=turn_number,
            username=user.username,
            word=guess.word,
            result=[
                LetterResultOut(letter=letter, status=status)
                for letter, status in letter_results
            ],
            correct=is_correct,
            status=match.status,
            current_turn_username=current_turn_username,
            winner_username=winner_username,
        )

    def _record_scores(self, db: Session, match: Match) -> None:
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
                db, match.winner_id, GameScoreCreate(game="wordle_vs", score=winner_score)
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

    def _username_or_none(self, db: Session, user_id: int | None) -> str | None:
        if user_id is None:
            return None
        found = db.get(User, user_id)
        return found.username if found else None

    def to_public(self, db: Session, match: Match) -> MatchPublic:
        inviter = db.get(User, match.inviter_id)
        invitee = db.get(User, match.invitee_id)
        return MatchPublic(
            id=match.id,
            game=match.game,
            status=match.status,
            inviter_username=inviter.username if inviter else "",
            invitee_username=invitee.username if invitee else "",
            current_turn_username=self._username_or_none(db, match.current_turn_user_id),
            winner_username=self._username_or_none(db, match.winner_id),
            max_guesses=match.max_guesses,
            created_at=match.created_at,
            started_at=match.started_at,
            completed_at=match.completed_at,
        )

    def to_detail(self, db: Session, match: Match) -> MatchDetail:
        public = self.to_public(db, match)
        guesses = (
            db.query(MatchGuess)
            .filter(MatchGuess.match_id == match.id)
            .order_by(MatchGuess.turn_number)
            .all()
        )
        guess_results = []
        for g in guesses:
            guesser = db.get(User, g.user_id)
            guess_results.append(
                MatchGuessResult(
                    match_id=match.id,
                    turn_number=g.turn_number,
                    username=guesser.username if guesser else "",
                    word=g.word,
                    result=[
                        LetterResultOut(letter=letter, status=status)
                        for letter, status in zip(g.word, g.result)
                    ],
                    correct=g.is_correct,
                    status=match.status,
                    current_turn_username=public.current_turn_username,
                    winner_username=public.winner_username,
                )
            )
        return MatchDetail(
            **public.model_dump(),
            target_word=match.target_word if match.status == "completed" else None,
            guesses=guess_results,
        )

    def to_pending_invite(self, db: Session, match: Match) -> PendingInvite:
        inviter = db.get(User, match.inviter_id)
        return PendingInvite(
            id=match.id,
            game=match.game,
            from_username=inviter.username if inviter else "",
            created_at=match.created_at,
            expires_at=match.invite_expires_at,
        )


match = CRUDMatch(Match)
