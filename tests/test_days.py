from datetime import date, datetime, timedelta

from app.game.puzzles import day_of, day_start
from app.utils import generate_match_invite_email


def test_a_summer_day_starts_at_7am_utc():
    assert day_start(date(2026, 7, 1)) == datetime(2026, 7, 1, 7)


def test_a_winter_day_starts_at_8am_utc():
    assert day_start(date(2026, 1, 15)) == datetime(2026, 1, 15, 8)


def test_the_day_clocks_spring_forward_has_23_hours():
    assert day_start(date(2026, 3, 9)) - day_start(date(2026, 3, 8)) == timedelta(hours=23)


def test_the_day_clocks_fall_back_has_25_hours():
    assert day_start(date(2026, 11, 2)) - day_start(date(2026, 11, 1)) == timedelta(hours=25)


def test_a_pacific_evening_belongs_to_that_day():
    # 01:00 UTC on Jul 2 is 6pm on Jul 1 in Los Angeles.
    assert day_of(datetime(2026, 7, 2, 1)) == date(2026, 7, 1)


def test_a_day_runs_from_its_start_up_to_the_next():
    start = day_start(date(2026, 7, 1))
    assert day_of(start) == date(2026, 7, 1)
    assert day_of(start - timedelta(microseconds=1)) == date(2026, 6, 30)


def test_invite_email_gives_the_pacific_expiry_date():
    email = generate_match_invite_email(
        inviter_username="inviter",
        game_title="Wordle",
        message=None,
        link="https://example.com/invite/x",
        expires_at=datetime(2026, 9, 22, 3),  # 8pm Sep 21 in Los Angeles
    )
    assert "September 21" in email.html_content
