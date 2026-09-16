from datetime import timedelta

from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.crud.game_score import game_score as crud_game_score
from app.crud.match_puzzle import match_puzzle as crud_match_puzzle
from app.game import match_modes, wordle
from app.models.match import Match, MatchGuess, MatchPuzzle
from app.models.user import User
from app.schemas.game_score import GameScoreCreate
from app.schemas.match import (
    ActiveMatch,
    LetterResultOut,
    MatchDetail,
    MatchGuessResult,
    MatchHistory,
    MatchHistoryItem,
    MatchInviteCreate,
    MatchPublic,
    MatchRecord,
    PendingInvite,
)
from app.utils import utcnow

OPEN_STATUSES = ("pending_invite", "in_progress")
# The one game both players win or lose together, so it has no winner.
COOP_GAME = "sudoku_coop"


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

    def get_history(
        self, db: Session, user_id: int, *, skip: int, limit: int
    ) -> MatchHistory:
        finished = db.query(Match).filter(
            or_(Match.inviter_id == user_id, Match.invitee_id == user_id),
            Match.status == "completed",
        )
        rows = (
            # id breaks ties so pages stay stable when two matches share a timestamp.
            finished.order_by(Match.completed_at.desc(), Match.id.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        # COUNT skips the NULLs a CASE without ELSE yields, so each counts its own rows.
        won, lost, drawn = (
            finished.filter(Match.game != COOP_GAME)
            .with_entities(
                func.count(case((Match.winner_id == user_id, 1))),
                func.count(case((and_(Match.winner_id.isnot(None), Match.winner_id != user_id), 1))),
                func.count(case((Match.winner_id.is_(None), 1))),
            )
            .one()
        )
        return MatchHistory(
            items=[self._to_history_item(db, m, user_id) for m in rows],
            total=finished.count(),
            record=MatchRecord(won=won, lost=lost, drawn=drawn),
        )

    def _to_history_item(self, db: Session, match: Match, viewer_id: int) -> MatchHistoryItem:
        opponent_id = match.invitee_id if viewer_id == match.inviter_id else match.inviter_id
        if match.game == COOP_GAME:
            # Co-op completes with no winner; the puzzle row says how it ended.
            result = crud_match_puzzle.get_by_match(db, match.id).state["outcome"]
        elif match.winner_id is None:
            result = "draw"
        else:
            result = "won" if match.winner_id == viewer_id else "lost"
        return MatchHistoryItem(
            id=match.id,
            game=match.game,
            opponent_username=self._username_or_none(db, opponent_id) or "",
            result=result,
            # Completed matches always carry completed_at.
            completed_at=match.completed_at or match.created_at,
        )

    def accept_invite(self, db: Session, match: Match) -> Match:
        match.status = "in_progress"
        match.started_at = utcnow()
        # Races and co-op have no turns: both players move whenever they like.
        match.current_turn_user_id = match.inviter_id if match.game == "wordle" else None
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
        result_values = wordle.evaluate_guess(match.target_word, word)
        letter_results = list(zip(word, result_values, strict=True))

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
            match.completed_at = utcnow()
            match.current_turn_user_id = None
        elif turn_number >= match.max_guesses:
            match.status = "completed"
            match.winner_id = None
            match.completed_at = utcnow()
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

    def to_detail(self, db: Session, match: Match, viewer_id: int) -> MatchDetail:
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
                        for letter, status in zip(g.word, g.result, strict=True)
                    ],
                    correct=g.is_correct,
                    status=match.status,
                    current_turn_username=public.current_turn_username,
                    winner_username=public.winner_username,
                )
            )
        return MatchDetail(
            **public.model_dump(),
            target_word=(
                match.target_word
                if match.game == "wordle" and match.status == "completed"
                else None
            ),
            guesses=guess_results,
            race=(
                crud_match_puzzle.race_detail(db, match, viewer_id)
                if match.game in match_modes.RACE_GAMES
                else None
            ),
            sudoku=(
                crud_match_puzzle.sudoku_detail(db, match)
                if match.game == match_modes.SUDOKU_COOP
                else None
            ),
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

    def get_active_items(self, db: Session, user_id: int) -> list[ActiveMatch]:
        """`get_active` as `ActiveMatch`es, in a fixed number of queries.

        The rail polls this on every match-list change, so opponents and race puzzles
        are loaded in one query each rather than per match.
        """
        matches = self.get_active(db, user_id)
        opponent_ids = {
            m.invitee_id if user_id == m.inviter_id else m.inviter_id for m in matches
        }
        usernames = (
            {
                row.id: row.username
                for row in db.query(User.id, User.username)
                .filter(User.id.in_(opponent_ids))
                .all()
            }
            if opponent_ids
            else {}
        )
        puzzles = crud_match_puzzle.get_by_matches(
            db,
            [
                m.id
                for m in matches
                if m.status != "pending_invite" and m.game in match_modes.RACE_GAMES
            ],
        )
        return [self.to_active_item(m, user_id, usernames, puzzles) for m in matches]

    def to_active_item(
        self,
        match: Match,
        viewer_id: int,
        usernames: dict[int, str],
        puzzles: dict[int, MatchPuzzle],
    ) -> ActiveMatch:
        """One rail entry, built from the maps `get_active_items` preloaded."""
        is_inviter = viewer_id == match.inviter_id
        opponent_id = match.invitee_id if is_inviter else match.inviter_id
        pending = match.status == "pending_invite"
        row = puzzles.get(match.id)
        if pending:
            your_move = not is_inviter
        elif match.game == "wordle":
            your_move = match.current_turn_user_id == viewer_id
        elif match.game in match_modes.RACE_GAMES and row is not None:
            your_move = not crud_match_puzzle.race_finished_in(row, match, viewer_id)
        else:
            # Co-op has no turns: the shared board can always use another hand.
            your_move = True
        return ActiveMatch(
            id=match.id,
            game=match.game,
            status="pending_invite" if pending else "in_progress",
            opponent_username=usernames.get(opponent_id, ""),
            role="inviter" if is_inviter else "invitee",
            your_move=your_move,
            created_at=match.created_at,
            started_at=match.started_at,
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
