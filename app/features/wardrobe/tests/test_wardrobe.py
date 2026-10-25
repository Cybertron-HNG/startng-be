from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import events, manifest
from app.config import settings
from app.db import Base, get_db
from app.features.integrity import service as integrity
from app.features.progress import service as progress
from app.features.wardrobe import goals, rewards, service
from app.features.wardrobe.catalog import BY_KEY, on_sale
from app.features.wardrobe.models import CoinEntry, GameGoal
from app.main import app
from app.models import Game, Score, Tournament, TournamentPoints, User
from app.security import create_token

HIGH = [{"key": "score", "label": "Score", "order": "highest", "format": "number"}]
NAIRA = [{"key": "naira", "label": "Naira", "order": "highest", "format": "number"},
         {"key": "time", "label": "Time", "order": "lowest", "format": "time"}]
PLAYERS = ("ada", "tunde", "boss")
GAMES = ("danfo", "lane-dash", "relic-hunter", "oven-rush", "word-up", "snake")


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(settings, "features", "progress,integrity,wardrobe")
    monkeypatch.setattr(settings, "super_admin_emails", "boss@example.com")
    monkeypatch.setattr(rewards, "SessionLocal", Session)  # the event handlers open their own session
    monkeypatch.setattr(progress, "_visibility", {})  # another test's privacy hook mustn't hide our players
    integrity.reset()
    service.reset()
    goals.reset()
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = [User(email=f"{n}@example.com", name=n.title(), google_sub=f"g-{n}") for n in PLAYERS]
        db.add_all(users)
        db.flush()
        db.add_all([Game(name=g, owner_id=users[0].id, repo_url=f"https://github.com/x/{g}",
                         leaderboard=NAIRA if g == "danfo" else HIGH) for g in GAMES])
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
        ids = {u.name.lower(): u.id for u in users}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()
    integrity.reset()
    service.reset()
    goals.reset()


def play(client, tokens, who, game, value, time_=100):
    stats = {"naira": value, "time": time_} if game == "danfo" else {"score": value}
    res = client.post(f"/games/{game}/scores", json={"stats": stats}, headers=tokens[who])
    assert res.status_code == 200, res.text
    return res.json()


def wallet(client, tokens, who):
    res = client.get("/me/wallet", headers=tokens[who])
    assert res.status_code == 200, res.text
    return res.json()


def add_players(Session, game, values, stats_of=lambda v: {"score": v}):
    """Other players with a best on the game's board."""
    with Session() as db:
        for n, v in enumerate(values):
            u = User(email=f"p{game}{n}@example.com", name=f"P{n}")
            db.add(u)
            db.flush()
            db.add(Score(user_id=u.id, game_name=game, best=v, stats=stats_of(v)))
        db.commit()


def test_off_without_its_flag(env, monkeypatch):
    client, tokens, *_ = env
    monkeypatch.setattr(settings, "features", "progress,integrity")
    assert client.get("/shop").status_code == 404
    assert client.get("/me/wallet", headers=tokens["ada"]).status_code == 404
    play(client, tokens, "ada", "snake", 5)  # runs still work, and pay nothing
    monkeypatch.setattr(settings, "features", "progress,integrity,wardrobe")
    assert wallet(client, tokens, "ada")["balance"] == 0


def test_playing_pays_the_first_run_of_the_day_and_each_new_game_once(env):
    client, tokens, *_ = env
    play(client, tokens, "ada", "snake", 5)
    assert wallet(client, tokens, "ada")["balance"] == rewards.FIRST_RUN + rewards.NEW_GAME
    play(client, tokens, "ada", "snake", 6)  # same day, same game: nothing more
    assert wallet(client, tokens, "ada")["balance"] == rewards.FIRST_RUN + rewards.NEW_GAME
    play(client, tokens, "ada", "word-up", 6)
    w = wallet(client, tokens, "ada")
    assert w["balance"] == rewards.FIRST_RUN + 2 * rewards.NEW_GAME
    assert [e["reason"] for e in w["entries"]] == ["new_game", "new_game", "first_run"]
    assert w["earned_today"] == w["balance"] and w["daily_cap"] == service.DAILY_CAP


def test_coins_from_playing_stop_at_the_daily_cap(env, monkeypatch):
    client, tokens, *_ = env
    monkeypatch.setattr(service, "DAILY_CAP", 50)
    for game in ("snake", "word-up", "oven-rush"):
        play(client, tokens, "ada", game, 5)
    w = wallet(client, tokens, "ada")
    assert w["balance"] == 50 and w["earned_today"] == 50  # 10 + 25 + 15 (cut), then nothing


def test_explorer_cap_is_awarded_after_five_games_and_shows_in_the_score_reply(env):
    client, tokens, *_ = env
    for game in GAMES[1:5]:
        assert {"kind": "item", "key": "explorer_cap"} not in play(client, tokens, "ada", game, 5)["unlocked"]
    assert {"kind": "item", "key": "explorer_cap"} in play(client, tokens, "ada", "snake", 5)["unlocked"]
    mine = client.get("/me/wardrobe", headers=tokens["ada"]).json()
    assert {"key": "explorer_cap", "source": "earned"} in mine["owned"]
    service.reset()
    shares = {i["key"]: i["owned_share"] for i in client.get("/wardrobe/items").json()}
    assert shares["explorer_cap"] == 1.0  # ada is the only player with a score


def test_the_shop_sells_todays_stock_and_never_epic_items(env):
    client, tokens, ids, Session = env
    shop = client.get("/shop").json()
    assert len(shop["featured"]) == service.FEATURED
    for item in shop["featured"]:
        assert item["price"] == int(round(BY_KEY[item["key"]].price * (1 - service.FEATURED_DISCOUNT), -1))
    on_sale_keys = {i["key"] for i in shop["featured"] + shop["items"]}
    assert all(BY_KEY[k].rarity in ("common", "uncommon", "rare", "limited") for k in on_sale_keys)
    assert "silver_crown" not in on_sale_keys and "explorer_cap" not in on_sale_keys

    cheapest = min(shop["featured"] + shop["items"], key=lambda i: i["price"])
    res = client.post("/shop/buy", json={"item": cheapest["key"]}, headers=tokens["ada"])
    assert res.status_code == 422 and "more coins" in res.json()["detail"]
    with Session() as db:
        service.pay(db, ids["ada"], 1000, "test", "gift")
    res = client.post("/shop/buy", json={"item": cheapest["key"]}, headers=tokens["ada"])
    assert res.status_code == 200 and res.json()["balance"] == 1000 - cheapest["price"]
    again = client.post("/shop/buy", json={"item": cheapest["key"]}, headers=tokens["ada"])
    assert again.status_code == 422 and again.json()["detail"] == "You already have it"
    assert client.post("/shop/buy", json={"item": "gem_crown"}, headers=tokens["ada"]).status_code == 422
    assert wallet(client, tokens, "ada")["entries"][0] == {**wallet(client, tokens, "ada")["entries"][0],
                                                          "amount": -cheapest["price"], "reason": "shop"}


def test_limited_items_are_only_on_sale_in_their_window():
    hat = BY_KEY["detty_december_hat"]
    assert not on_sale(hat, date(2026, 11, 30))
    assert on_sale(hat, date(2026, 12, 25))
    assert not on_sale(hat, date(2027, 1, 1))
    assert not on_sale(BY_KEY["pioneer_pin"], date(2026, 12, 25))  # given, never sold


def test_wearing_items_changes_what_games_see(env):
    client, tokens, ids, Session = env
    res = client.post("/me/wardrobe/wear", json={"item": "gele"}, headers=tokens["ada"])
    assert res.status_code == 422  # not hers
    with Session() as db:
        service.grant(db, ids["ada"], "gele", source="admin")
        service.grant(db, ids["ada"], "gold_chain", source="admin")
    client.post("/me/wardrobe/wear", json={"item": "gele"}, headers=tokens["ada"])
    mine = client.post("/me/wardrobe/wear", json={"item": "gold_chain"}, headers=tokens["ada"]).json()
    assert mine["worn"] == {"hat": "gele", "neck": "gold_chain"}
    assert mine["style"] == {"hat": "gele", "items": [{"key": "gele", "slot": "hat", "rarity": "rare"},
                                                      {"key": "gold_chain", "slot": "neck", "rarity": "rare"}]}
    mine = client.post("/me/wardrobe/take-off", json={"slot": "hat"}, headers=tokens["ada"]).json()
    assert mine["worn"] == {"neck": "gold_chain"} and "hat" not in mine["style"]
    public = client.get(f"/players/{ids['ada']}/wardrobe").json()
    assert public == {"worn": {"neck": "gold_chain"}, "showcase": ["gele", "gold_chain"], "items_owned": 2}
    assert client.get("/players/9999/wardrobe").status_code == 404


def test_rewards_goals_in_startng_json_are_checked(monkeypatch):
    monkeypatch.setattr(settings, "features", "wardrobe")
    with pytest.raises(manifest.SchemaError, match="goals"):
        goals.parse({"goal": []})
    with pytest.raises(manifest.SchemaError, match="at_least"):
        goals.parse({"goals": [{"key": "a", "label": "A", "stat": "score"}]})
    with pytest.raises(manifest.SchemaError, match="unique"):
        goals.parse({"goals": [{"key": "a", "label": "A", "stat": "score", "at_least": 1}] * 2})
    with pytest.raises(manifest.SchemaError, match="at most"):
        goals.parse({"goals": [{"key": f"g{i}", "label": "A", "stat": "score", "at_least": i} for i in range(11)]})
    parsed = goals.parse({"goals": [{"key": "fast", "label": " Under 2 min ", "stat": "time", "at_most": 120,
                                     "repeat": "daily", "coins": 99999}]})
    assert parsed == [{"key": "fast", "label": "Under 2 min", "stat": "time", "at_least": False, "target": 120, "daily": True}]


def test_off_flag_ignores_the_rewards_section(monkeypatch):
    monkeypatch.setattr(settings, "features", "progress")
    assert goals.parse("anything") is None


def _save_goals(Session, game, section):
    with Session() as db:
        goals.apply(db, db.get(Game, game), goals.parse(section))
        db.commit()


def test_goals_pay_by_how_hard_they_are(env):
    client, tokens, ids, Session = env
    _save_goals(Session, "danfo", {"goals": [
        {"key": "rich", "label": "Earn 20,000", "stat": "naira", "at_least": 20000},
        {"key": "fast", "label": "Under 2 minutes", "stat": "time", "at_most": 120, "repeat": "daily"},
        {"key": "ghost", "label": "Not a stat", "stat": "nope", "at_least": 1},  # dropped: not on the board
    ]})
    listed = client.get("/games/danfo/goals").json()
    assert [g["key"] for g in listed] == ["rich", "fast"]
    assert listed[0]["tier"] == "new" and listed[0]["coins"] == goals.NEW_GAME[1]
    assert listed[1]["coins"] == goals.NEW_GAME[1] // 2  # daily goals pay half

    # 40 players: 1 of them reaches 20,000 (2.5%: very hard); all of them finish under 2 minutes (easy)
    add_players(Session, "danfo", [1000] * 39 + [25000], stats_of=lambda v: {"naira": v, "time": 90})
    goals.reset()
    by_key = {g["key"]: g for g in client.get("/games/danfo/goals").json()}
    assert (by_key["rich"]["tier"], by_key["rich"]["coins"]) == ("very_hard", 100)
    assert (by_key["fast"]["tier"], by_key["fast"]["coins"]) == ("easy", 5)

    play(client, tokens, "ada", "danfo", 21000, time_=100)
    entries = {(e["reason"], e["ref"]): e["amount"] for e in wallet(client, tokens, "ada")["entries"]}
    today = service.today().isoformat()
    assert entries[("game_goal", "danfo:rich")] == 100
    assert entries[("game_goal", f"danfo:fast:{today}")] == 5
    play(client, tokens, "ada", "danfo", 22000, time_=100)  # both already paid (once, and once today)
    assert len([e for e in wallet(client, tokens, "ada")["entries"] if e["reason"] == "game_goal"]) == 2


def test_top_places_on_a_busy_board_pay_coins_and_crowns(env):
    client, tokens, ids, Session = env
    add_players(Session, "snake", list(range(100, 130)))  # 30 others: a full board
    play(client, tokens, "ada", "snake", 140)
    play(client, tokens, "ada", "snake", 140)  # #1 held across two runs
    entries = {e["reason"]: e["amount"] for e in wallet(client, tokens, "ada")["entries"]}
    assert entries["top_10"] == rewards.TOP_10 and entries["first_place"] == rewards.FIRST_PLACE
    owned = {o["key"] for o in client.get("/me/wardrobe", headers=tokens["ada"]).json()["owned"]}
    assert {"silver_crown", "gem_crown"} <= owned


def test_top_places_on_a_small_board_pay_less_and_no_crown(env):
    client, tokens, ids, Session = env
    add_players(Session, "word-up", [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140])  # 14 others
    play(client, tokens, "ada", "word-up", 150)
    play(client, tokens, "ada", "word-up", 150)
    entries = {e["reason"]: e["amount"] for e in wallet(client, tokens, "ada")["entries"]}
    assert entries["first_place"] == round(rewards.FIRST_PLACE * 15 / rewards.FULL_BOARD)
    owned = {o["key"] for o in client.get("/me/wardrobe", headers=tokens["ada"]).json()["owned"]}
    assert "gem_crown" not in owned


def test_tournament_top_three_and_challenges_pay(env):
    client, tokens, ids, Session = env
    with Session() as db:
        start = datetime(2026, 10, 1, 17, tzinfo=timezone.utc)
        t = Tournament(starts_at=start, ends_at=start + timedelta(days=1), winner_id=ids["ada"])
        db.add(t)
        db.flush()
        db.add_all([TournamentPoints(tournament_id=t.id, user_id=ids["ada"], points=30, firsts=3, place=1),
                    TournamentPoints(tournament_id=t.id, user_id=ids["tunde"], points=20, firsts=1, place=2)])
        db.commit()
        tid = t.id
    events.publish("tournament_finished", {"tournament_id": tid, "winner_id": ids["ada"], "players": 5})
    events.publish("tournament_finished", {"tournament_id": tid, "winner_id": ids["ada"], "players": 5})  # paid once
    assert wallet(client, tokens, "ada")["balance"] == 150  # 300, halved: only 5 players
    assert wallet(client, tokens, "tunde")["balance"] == 100
    events.publish("challenge_beaten", {"challenge_id": 7, "challenger_id": ids["ada"], "user_id": ids["tunde"]})
    assert wallet(client, tokens, "tunde")["balance"] == 100 + rewards.CHALLENGE
    owned = {o["key"] for o in client.get("/me/wardrobe", headers=tokens["tunde"]).json()["owned"]}
    assert "champion_sash" in owned
    owned = {o["key"] for o in client.get("/me/wardrobe", headers=tokens["ada"]).json()["owned"]}
    assert "tournament_cape" in owned


def test_admins_give_and_take_back_coins_and_items(env):
    client, tokens, ids, Session = env
    body = {"user_id": ids["ada"], "amount": 500, "note": "launch gift"}
    assert client.post("/admin/wardrobe/coins", json=body, headers=tokens["tunde"]).status_code == 404  # not an admin
    assert client.post("/admin/wardrobe/coins", json=body, headers=tokens["boss"]).json()["balance"] == 500
    body = {"user_id": ids["ada"], "amount": -9000, "note": "cheating"}
    assert client.post("/admin/wardrobe/coins", json=body, headers=tokens["boss"]).json()["balance"] == 0  # never below zero
    res = client.post("/admin/wardrobe/items", json={"user_id": ids["ada"], "item": "pioneer_pin"}, headers=tokens["boss"])
    assert {"key": "pioneer_pin", "source": "admin"} in res.json()["owned"]


def test_paying_the_same_thing_twice_pays_once(env):
    client, tokens, ids, Session = env
    with Session() as db:
        assert service.pay(db, ids["ada"], 40, "streak", "7:2026-10-22") == 40
        assert service.pay(db, ids["ada"], 40, "streak", "7:2026-10-22") == 0
        assert service.pay(db, ids["ada"], 0, "streak", "x") == 0
        assert service.balance(db, ids["ada"]) == 40
        assert db.scalar(select(CoinEntry.amount).where(CoinEntry.user_id == ids["ada"])) == 40


def test_the_live_room_shows_what_players_wear(env, monkeypatch):
    from app.routers import live
    client, tokens, ids, Session = env
    look = {"body": "female", "hat": "cap", "accessory": "none", "primaryColor": "#1dd1a1"}
    with Session() as db:
        db.get(User, ids["ada"]).character = {"name": "Ada", "style": look}
        db.commit()
        service.grant(db, ids["ada"], "gele", source="admin")
        service.wear(db, ids["ada"], "gele")
    worn = live._load(Session, ids["ada"], "snake").style
    assert worn["hat"] == "gele" and worn["primaryColor"] == "#1dd1a1"
    assert worn["items"] == [{"key": "gele", "slot": "hat", "rarity": "rare"}]
    assert live._load(Session, ids["tunde"], "snake").style is None  # no character yet: the starting look, as before
    monkeypatch.setattr(settings, "features", "progress,integrity")
    assert live._load(Session, ids["ada"], "snake").style == look  # the wardrobe off: exactly as before


def test_the_shop_text_for_search_engines(env, monkeypatch):
    client, tokens, ids, Session = env
    shop = client.get("/shop").json()
    text = client.get("/pages/shop").text
    assert "<h1>Shop</h1>" in text and "Epic and Legendary items can't be bought" in text
    first = shop["featured"][0]
    assert (f'<li class="wd-item r-{first["rarity"]}"><strong>{first["name"]}</strong>' in text
            and f'🪙 {first["price"]:,}<span class="tag">20% off</span>' in text)
    assert all(f"<strong>{i['name']}</strong>" in text for i in shop["items"])
    assert "Nobody has it yet" in text
    monkeypatch.setattr(settings, "features", "progress,integrity")
    assert client.get("/pages/shop").status_code == 404
