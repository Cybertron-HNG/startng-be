import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.features.crews import service
from app.main import app
from app.models import User
from app.security import create_token

PLAYERS = ("ada", "tunde", "chioma", "musa", "kofi")


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

    monkeypatch.setattr(settings, "features", "*")
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        users = [User(email=f"{n}@example.com", name=n.title()) for n in PLAYERS]
        db.add_all(users)
        db.commit()
        tokens = {u.name.lower(): {"Authorization": f"Bearer {create_token(u.id)}"} for u in users}
        ids = {u.name.lower(): u.id for u in users}
    yield TestClient(app), tokens, ids, Session
    app.dependency_overrides.clear()


def make(client, tokens, who="ada", name="Lagos Family", kind="family"):
    return client.post("/crews", json={"name": name, "kind": kind}, headers=tokens[who])


def test_off_without_its_flag(env, monkeypatch):
    client, tokens, _, _ = env
    monkeypatch.setattr(settings, "features", "")
    assert make(client, tokens).status_code == 404


def test_create_makes_the_maker_a_member(env):
    client, tokens, ids, _ = env
    res = make(client, tokens)
    assert res.status_code == 201, res.text
    body = res.json()
    assert (body["name"], body["kind"], body["members"], body["mine"]) == ("Lagos Family", "family", 1, True)
    assert body["people"][0]["player_id"] == ids["ada"] and body["people"][0]["owner"] is True
    assert len(body["code"]) >= 11  # a link that can't be guessed
    assert make(client, tokens, who="ada").status_code == 201


@pytest.mark.parametrize("name,kind", [("ab", "family"), ("x" * 41, "family"), ("Fine name", "company")])
def test_create_rules(env, name, kind):
    client, tokens, _, _ = env
    assert make(client, tokens, name=name, kind=kind).status_code == 422


def test_needs_sign_in(env):
    client, tokens, _, _ = env
    assert client.post("/crews", json={"name": "No one", "kind": "city"}).status_code == 401


def test_the_link_shows_a_little_and_joining_opens_the_crew(env):
    client, tokens, _, _ = env
    crew = make(client, tokens).json()
    seen = client.get(f"/crews/join/{crew['code']}", headers=tokens["tunde"]).json()
    assert (seen["name"], seen["owner"], seen["members"], seen["joined"], seen["full"]) == ("Lagos Family", "Ada", 1, False, False)
    assert "people" not in seen and "id" not in seen  # not the member list
    assert client.get(f"/crews/{crew['id']}", headers=tokens["tunde"]).status_code == 404  # a crew is private
    assert client.post(f"/crews/join/{crew['code']}", headers=tokens["tunde"]).status_code == 200
    page = client.get(f"/crews/{crew['id']}", headers=tokens["tunde"]).json()
    assert [p["name"] for p in page["people"]] == ["Ada", "Tunde"] and page["mine"] is False


def test_unknown_links_and_signed_out_joins(env):
    client, tokens, _, _ = env
    crew = make(client, tokens).json()
    assert client.get("/crews/join/nope-nope-1").status_code == 404
    assert client.post("/crews/join/nope-nope-1", headers=tokens["tunde"]).status_code == 404
    assert client.post(f"/crews/join/{crew['code']}").status_code == 401


def test_joining_twice_is_fine(env):
    client, tokens, _, _ = env
    crew = make(client, tokens).json()
    for _ in range(2):
        assert client.post(f"/crews/join/{crew['code']}", headers=tokens["tunde"]).json()["members"] == 2


def test_member_cap(env, monkeypatch):
    client, tokens, _, _ = env
    monkeypatch.setattr(service, "MAX_MEMBERS", 2)
    crew = make(client, tokens).json()
    assert client.post(f"/crews/join/{crew['code']}", headers=tokens["tunde"]).status_code == 200
    assert client.post(f"/crews/join/{crew['code']}", headers=tokens["chioma"]).status_code == 400
    assert client.get(f"/crews/join/{crew['code']}").json()["full"] is True


def test_five_crews_at_most_per_player(env):
    client, tokens, _, _ = env
    for i in range(5):
        assert make(client, tokens, name=f"Crew number {i}").status_code == 201
    assert make(client, tokens, name="One too many").status_code == 400
    other = make(client, tokens, who="tunde", name="Tundes crew").json()
    assert client.post(f"/crews/join/{other['code']}", headers=tokens["ada"]).status_code == 400  # Ada is in five already


def test_leave_and_the_maker_cannot_leave(env):
    client, tokens, _, _ = env
    crew = make(client, tokens).json()
    client.post(f"/crews/join/{crew['code']}", headers=tokens["tunde"])
    assert client.delete(f"/crews/{crew['id']}/members/me", headers=tokens["tunde"]).status_code == 204
    assert client.get(f"/crews/{crew['id']}", headers=tokens["tunde"]).status_code == 404
    assert client.delete(f"/crews/{crew['id']}/members/me", headers=tokens["ada"]).status_code == 400


def test_only_the_maker_removes_people(env):
    client, tokens, ids, _ = env
    crew = make(client, tokens).json()
    for who in ("tunde", "chioma"):
        client.post(f"/crews/join/{crew['code']}", headers=tokens[who])
    assert client.delete(f"/crews/{crew['id']}/members/{ids['chioma']}", headers=tokens["tunde"]).status_code == 403
    assert client.delete(f"/crews/{crew['id']}/members/{ids['ada']}", headers=tokens["ada"]).status_code == 400
    assert client.delete(f"/crews/{crew['id']}/members/{ids['chioma']}", headers=tokens["ada"]).status_code == 204
    assert [p["name"] for p in client.get(f"/crews/{crew['id']}", headers=tokens["ada"]).json()["people"]] == ["Ada", "Tunde"]
    assert client.get(f"/crews/{crew['id']}", headers=tokens["chioma"]).status_code == 404


def test_new_link_kills_the_old_one_and_members_stay(env):
    client, tokens, _, _ = env
    crew = make(client, tokens).json()
    client.post(f"/crews/join/{crew['code']}", headers=tokens["tunde"])
    assert client.post(f"/crews/{crew['id']}/new-link", headers=tokens["tunde"]).status_code == 403
    new = client.post(f"/crews/{crew['id']}/new-link", headers=tokens["ada"]).json()["code"]
    assert new != crew["code"]
    assert client.get(f"/crews/join/{crew['code']}").status_code == 404
    assert client.get(f"/crews/{crew['id']}", headers=tokens["tunde"]).json()["members"] == 2


def test_only_the_maker_deletes_and_it_takes_the_members_with_it(env):
    client, tokens, _, Session = env
    crew = make(client, tokens).json()
    client.post(f"/crews/join/{crew['code']}", headers=tokens["tunde"])
    assert client.delete(f"/crews/{crew['id']}", headers=tokens["tunde"]).status_code == 403
    assert client.delete(f"/crews/{crew['id']}", headers=tokens["ada"]).status_code == 204
    assert client.get(f"/crews/join/{crew['code']}").status_code == 404  # the invite is gone with it
    with Session() as db:
        assert service.crew_ids_of(db, 1) == [] and service.crew_ids_of(db, 2) == []


def test_my_crews_and_the_contract_other_features_use(env):
    client, tokens, ids, Session = env
    a = make(client, tokens, name="Alpha crew").json()
    b = make(client, tokens, who="tunde", name="Beta crew", kind="school").json()
    client.post(f"/crews/join/{b['code']}", headers=tokens["ada"])
    assert [c["name"] for c in client.get("/crews/mine", headers=tokens["ada"]).json()] == ["Alpha crew", "Beta crew"]
    assert client.get("/crews/mine", headers=tokens["chioma"]).json() == []
    with Session() as db:
        assert sorted(service.crew_ids_of(db, ids["ada"])) == sorted([a["id"], b["id"]])
        assert sorted(service.members_of(db, b["id"])) == sorted([ids["ada"], ids["tunde"]])
        assert service.is_member(db, a["id"], ids["ada"]) and not service.is_member(db, a["id"], ids["tunde"])
