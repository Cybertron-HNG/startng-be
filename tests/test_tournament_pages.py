"""/pages/tournament and /pages/leaderboard: the text nginx puts inside #root of those pages (app/routers/pages.py)."""
from datetime import timedelta

from app.models import Game
from tests.test_tournaments import BOARD, END, clock, enter, env, play  # noqa: F401 (fixtures)


def test_tournament_text_has_the_games_standings_and_times(env):
    client, tokens, *_ = env
    enter(client, tokens, "ada")
    enter(client, tokens, "tunde")
    play(client, tokens, "ada", "g0", 50)
    play(client, tokens, "tunde", "g0", 1234)
    res = client.get("/pages/tournament")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/html")
    text = res.text
    assert "<html" not in text  # only what goes inside #root
    assert "<h1>Today's tournament</h1>" in text
    assert "Ends at <b>6:00 PM</b> Nigeria time. 2 players have entered." in text
    for i in range(5):
        assert f'<a class="game-link" href="/games/g{i}"><div class="info"><h3>G{i}</h3>' in text
    assert '<span class="nm">Tunde</span><span class="sc">1,234</span>' in text  # best first, with separators
    assert "<p>2 players</p>" in text and "No runs yet. Be the first." in text
    assert '<span class="rk">1</span><span class="nm">Tunde</span><span class="num">1</span><span class="num">10</span>' in text
    assert "The first winner is named when today's tournament ends." in text


def test_tournament_text_names_past_winners(env, clock):
    client, tokens, *_ = env
    enter(client, tokens, "ada")
    play(client, tokens, "ada", "g1", 9)
    clock.at = END + timedelta(hours=1)  # yesterday's has ended and today's has started
    text = client.get("/pages/tournament").text
    assert '<li><span class="when">Tue 20 Oct</span><span class="nm"><b>Runner Ada</b> · 10 pts</span></li>' in text
    assert "No points yet." in text


def test_no_tournament_says_so(env):
    client, _, _, Session = env
    with Session() as db:
        for g in db.query(Game).all():
            g.hidden = "reported"
        db.commit()
    assert "No tournament today" in client.get("/pages/tournament").text


def test_leaderboard_text_all_time_and_this_week(env, clock):
    client, tokens, *_ = env
    assert "No points yet." in client.get("/pages/leaderboard").text
    assert "No points this week." in client.get("/pages/leaderboard?period=week").text
    enter(client, tokens, "ada")
    play(client, tokens, "ada", "g0", 9)
    clock.at = END + timedelta(hours=1)
    text = client.get("/pages/leaderboard").text
    assert "<h1>Leaderboard</h1>" in text
    assert '<a href="/leaderboard" aria-current="page">All time</a><a href="/leaderboard?period=week">This week</a>' in text
    assert '<span class="rk">1</span><span class="nm">Runner Ada</span><span class="num">1</span><span class="num">1</span><span class="num">10</span>' in text
    week = client.get("/pages/leaderboard?period=week").text
    assert '<a href="/leaderboard?period=week" aria-current="page">This week</a>' in week and "Runner Ada" in week
    assert client.get("/pages/leaderboard?period=month").status_code == 422
