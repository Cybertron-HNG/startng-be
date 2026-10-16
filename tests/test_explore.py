"""Explore games: games hosted elsewhere, listed on start.ng. Anyone can submit, so most of these are about
what must be refused, and who may remove what."""
import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import explore
from app.config import settings
from app.db import Base, get_db
from app.main import app
from app.routers import explore as router

ADMIN = {"X-Admin-Token": "moderator-secret"}


def picture(width=1600, height=1000, kind="PNG", color=(70, 120, 200)) -> str:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, kind)
    return base64.b64encode(out.getvalue()).decode()


def game(**changes) -> dict:
    body = {"name": "Cassava Run", "creator": "Ngozi Bello", "url": "https://example.com/cassava-run",
            "description": "Race a delivery bike through the cassava fields. Dodge goats and beat the sunset.",
            "cover": picture()}
    body.update(changes)
    return body


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "explore_auto_approve", True)
    monkeypatch.setattr(settings, "explore_per_hour", 5)
    monkeypatch.setattr(settings, "explore_total_per_hour", 60)
    monkeypatch.setattr(settings, "explore_admin_token", "moderator-secret")
    router._attempts.clear()
    router._accepted.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def submit(client, **changes):
    return client.post("/explore/games", json=game(**changes))


def fields(res) -> dict:
    return {e["field"]: e["message"] for e in res.json()["errors"]}


# ---- the happy path

def test_a_submitted_game_is_listed_with_its_picture(client):
    assert client.get("/explore/games").json() == []
    res = submit(client)
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "approved" and body["manage_key"]
    shown = body["game"]
    assert (shown["name"], shown["creator"], shown["host"]) == ("Cassava Run", "Ngozi Bello", "example.com")
    listed = client.get("/explore/games").json()
    assert [g["id"] for g in listed] == [shown["id"]]
    assert "manage_key" not in listed[0] and "cover" not in listed[0]
    cover = client.get(listed[0]["cover_url"])
    assert cover.status_code == 200 and cover.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(cover.content)) as img:
        assert img.format == "WEBP" and img.size == (640, 400)  # drawn again at card size


def test_newest_first_and_search_by_name_or_creator(client):
    submit(client, name="Tower Stack", creator="Ada Okafor", url="https://example.com/tower")
    submit(client, name="Mango Dash", creator="Chidi Studios", url="https://example.org/mango")
    names = lambda q="": [g["name"] for g in client.get("/explore/games", params={"q": q}).json()]
    assert names() == ["Mango Dash", "Tower Stack"]
    assert names("ada") == ["Tower Stack"]
    assert names("MANGO") == ["Mango Dash"]
    assert names("tower okafor") == ["Tower Stack"]
    assert names("tower chidi") == []
    assert names("zzzz") == []


def test_text_is_cleaned(client):
    res = submit(client, name="  Tower\u200b   Stack \u202e", description="Line one is here for you.\r\n\r\n\r\n\r\nLine two is here too.")
    assert res.status_code == 201, res.text
    listed = client.get("/explore/games").json()[0]
    assert listed["name"] == "Tower Stack"
    assert listed["description"] == "Line one is here for you.\n\nLine two is here too."


# ---- the form's rules (every problem comes back at once, each tied to its field)

def test_an_empty_submission_explains_every_field(client):
    res = client.post("/explore/games", json={})
    assert res.status_code == 422
    assert res.json()["ok"] is False
    assert set(fields(res)) == {"name", "creator", "url", "description", "cover"}


def test_length_limits(client):
    assert "name" in fields(submit(client, name="x"))
    assert "name" in fields(submit(client, name="x" * 41))
    assert "creator" in fields(submit(client, creator="x"))
    assert "description" in fields(submit(client, description="too short"))
    assert "description" in fields(submit(client, description="x" * 501))
    assert submit(client, name="x" * 40).status_code == 201


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)", "data:text/html,<script>alert(1)</script>", "ftp://example.com/game", "file:///etc/passwd",
    "example.com/game", "not a url", "https://user:secret@example.com", "https://localhost:3000", "http://127.0.0.1/game",
    "https://192.168.1.5/game", "https://10.0.0.8/game", "https://169.254.169.254/latest", "https://[::1]/game",
    "https://intranet/game", "https://printer.local/game", "https://0x7f.1/game", "https://start.ng/hangout",
    "https://lanedash.start.ng", "https://example.com/" + "a" * 300, "https://exa mple.com", "https://-bad-.com",
])
def test_unsafe_or_unusable_links_are_refused(client, bad):
    res = submit(client, url=bad)
    assert res.status_code == 422, bad
    assert set(fields(res)) == {"url"}, bad


def test_links_are_tidied_and_both_schemes_work(client):
    res = submit(client, url="HTTPS://Example.COM/Play?level=2#top")
    assert res.status_code == 201, res.text
    assert res.json()["game"]["url"] == "https://example.com/Play?level=2"
    assert submit(client, name="Plain Http", url="http://example.org/game").status_code == 201
    unicode_host = submit(client, name="Unicode Host", url="https://b\u00fccher.example/game")
    assert unicode_host.status_code == 201 and unicode_host.json()["game"]["host"] == "xn--bcher-kva.example"


# ---- pictures

def test_bad_pictures_are_refused_whatever_they_claim_to_be(client):
    text = base64.b64encode(b"this is not an image").decode()
    assert "cover" in fields(submit(client, cover=text))
    assert "cover" in fields(submit(client, cover="data:image/png;base64," + text))
    svg = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="600"/>').decode()
    assert "cover" in fields(submit(client, cover="data:image/png;base64," + svg))
    gif = picture(kind="GIF")
    assert "cover" in fields(submit(client, cover=gif))
    assert "cover" in fields(submit(client, cover="%%% not base64 %%%"))
    assert "cover" in fields(submit(client, cover="data:image/png,raw-not-base64"))
    assert "300 x 200" in fields(submit(client, cover=picture(300, 200)))["cover"]
    assert "cover" in fields(submit(client, cover=picture(639, 1000)))


def test_a_big_picture_is_refused(client):
    png = base64.b64decode(picture())
    just_over = base64.b64encode(png + b"\0" * (2 * 1024 * 1024)).decode()  # a real PNG, plus padding, over 2 MB
    res = submit(client, cover=just_over)
    assert res.status_code == 422 and "2 MB" in fields(res)["cover"]
    way_over = base64.b64encode(png + b"\0" * (6 * 1024 * 1024)).decode()  # too big to even read the request
    assert submit(client, cover=way_over).status_code == 413


def test_png_jpeg_and_webp_are_all_fine_and_a_data_link_works(client):
    for i, kind in enumerate(("PNG", "JPEG", "WEBP")):
        res = submit(client, name=f"Game {kind}", url=f"https://example.com/{kind}", cover=picture(kind=kind))
        assert res.status_code == 201, (kind, res.text)
    assert submit(client, name="Data Link", url="https://example.com/dl", cover="data:image/png;base64," + picture()).status_code == 201


def test_a_huge_request_is_stopped_before_it_is_read(client):
    res = client.post("/explore/games", content=b"x" * (router.MAX_BODY + 10), headers={"content-type": "application/json"})
    assert res.status_code == 413
    assert client.post("/explore/games", content=b"not json").status_code == 400
    assert client.post("/explore/games", json=["a", "list"]).status_code == 400


# ---- repeats and floods

def test_the_same_game_cannot_be_listed_twice(client):
    assert submit(client).status_code == 201
    again = submit(client, url="https://example.net/other-link")
    assert again.status_code == 409 and "name" in fields(again)
    same_link = submit(client, name="Another Name", creator="Someone Else")
    assert same_link.status_code == 409 and "url" in fields(same_link)
    assert len(client.get("/explore/games").json()) == 1


def test_one_address_can_only_list_a_few_games_an_hour(client):
    for i in range(5):
        assert submit(client, name=f"Game {i}", url=f"https://example.com/{i}").status_code == 201
    res = submit(client, name="Game 6", url="https://example.com/6")
    assert res.status_code == 429 and res.json()["errors"][0]["code"] == "rate_limited"
    other = client.post("/explore/games", json=game(name="Elsewhere", url="https://example.com/e"), headers={"x-forwarded-for": "203.0.113.9"})
    assert other.status_code == 201  # a different address has its own allowance


def test_trying_over_and_over_is_slowed_down_too(client):
    for _ in range(router.ATTEMPTS_PER_HOUR):
        assert submit(client, name="x").status_code == 422
    assert submit(client, name="x").status_code == 429


def test_there_is_a_limit_across_everyone(client, monkeypatch):
    monkeypatch.setattr(settings, "explore_total_per_hour", 2)
    for i in range(2):
        res = client.post("/explore/games", json=game(name=f"Game {i}", url=f"https://example.com/{i}"), headers={"x-forwarded-for": f"203.0.113.{i}"})
        assert res.status_code == 201
    busy = client.post("/explore/games", json=game(name="Game 3", url="https://example.com/3"), headers={"x-forwarded-for": "203.0.113.77"})
    assert busy.status_code == 429 and busy.json()["errors"][0]["code"] == "busy"


# ---- removing a listing: the creator with their key, or a moderator

def test_the_creator_removes_their_own_game_with_the_key(client):
    made = submit(client).json()
    gid, key = made["game"]["id"], made["manage_key"]
    assert client.delete(f"/explore/games/{gid}").status_code == 401
    assert client.delete(f"/explore/games/{gid}", headers={"X-Manage-Key": "wrong"}).status_code == 403
    assert client.get("/explore/games").json() != []
    assert client.delete(f"/explore/games/{gid}", headers={"X-Manage-Key": key}).status_code == 204
    assert client.get("/explore/games").json() == []
    assert client.get(f"/explore/games/{gid}/cover").status_code == 404
    assert client.delete(f"/explore/games/{gid}", headers={"X-Manage-Key": key}).status_code == 404


def test_one_creators_key_does_not_remove_another_creators_game(client):
    first = submit(client).json()
    second = submit(client, name="Other Game", url="https://example.com/other").json()
    res = client.delete(f"/explore/games/{second['game']['id']}", headers={"X-Manage-Key": first["manage_key"]})
    assert res.status_code == 403


def test_a_moderator_can_remove_anything_and_only_with_the_right_token(client, monkeypatch):
    gid = submit(client).json()["game"]["id"]
    assert client.delete(f"/explore/games/{gid}", headers={"X-Admin-Token": "guess"}).status_code == 403
    assert client.delete(f"/explore/games/{gid}", headers=ADMIN).status_code == 204
    gid = submit(client, name="Second", url="https://example.com/second").json()["game"]["id"]
    monkeypatch.setattr(settings, "explore_admin_token", "")  # no moderators set up: an empty token is never a match
    assert client.delete(f"/explore/games/{gid}", headers={"X-Admin-Token": ""}).status_code == 401
    assert client.delete(f"/explore/games/{gid}", headers={"X-Admin-Token": "anything"}).status_code == 403


def test_the_key_is_not_stored_in_the_clear(client):
    key = submit(client).json()["manage_key"]
    from app.models import ExploreGame
    session = next(app.dependency_overrides[get_db]())
    stored = session.query(ExploreGame).one().manage_hash
    assert stored != key and key not in stored and explore.key_matches(key, stored)


# ---- reviewed mode (EXPLORE_AUTO_APPROVE=false)

def test_when_review_is_on_new_games_wait_for_a_moderator(client, monkeypatch):
    monkeypatch.setattr(settings, "explore_auto_approve", False)
    made = submit(client).json()
    assert made["status"] == "pending"
    gid = made["game"]["id"]
    assert client.get("/explore/games").json() == []
    assert client.get(f"/explore/games/{gid}/cover").status_code == 404
    assert client.get("/explore/games/pending").status_code == 403
    waiting = client.get("/explore/games/pending", headers=ADMIN).json()
    assert [g["id"] for g in waiting] == [gid] and waiting[0]["status"] == "pending"
    assert client.post(f"/explore/games/{gid}/approve").status_code == 403
    assert client.post(f"/explore/games/{gid}/approve", headers=ADMIN).status_code == 200
    assert [g["id"] for g in client.get("/explore/games").json()] == [gid]
    assert client.get(f"/explore/games/{gid}/cover").status_code == 200
    assert client.post("/explore/games/999/approve", headers=ADMIN).status_code == 404


# ---- the rules on their own

def test_matches_ignores_case_and_accents():
    assert explore.matches("Caf\u00e9 Run", "Ng\u1ecdzi", "cafe ngozi")  # Yoruba names match plain letters too
    assert explore.matches("Caf\u00e9 Run", "Ada", "CAFE run")
    assert explore.matches("Anything", "Anyone", "")
