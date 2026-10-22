from datetime import timedelta

from sqlalchemy import select

from app.features.email import newsletter, service
from app.features.email.models import Email, Newsletter
from app.features.email.provider import LogProvider
from app.features.email.tests.test_email import env, send, statuses  # noqa: F401  (env is a fixture)
from app.models import User

LETTER = {"subject": "What's new on start.ng", "body": "# Hello\n\nThree new games.\nPlay at [start.ng](https://start.ng/games)."}


def subscribe(Session, ids, who="ada", topics=("newsletter",)):
    with Session() as db:
        service.set_my_topics(db, db.get(User, ids[who]), list(topics))


def test_body_is_plain_text_with_links_only():
    html, text = newsletter.render_body('# Hi <b>\n\n<script>alert(1)</script>\nSee [us](https://start.ng) and [bad](javascript:alert(1))')
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<a href="https://start.ng"' in html and "javascript:" not in html.replace("[bad](javascript:alert(1))", "")
    assert "us (https://start.ng)" in text and text.startswith("HI <B>")


def test_only_admins_with_the_permission_can_write(env):
    client, tokens, ids, Session = env
    assert client.get("/admin/newsletters", headers=tokens["ada"]).status_code == 404
    assert client.post("/admin/newsletters", json=LETTER, headers=tokens["ada"]).status_code == 404
    assert client.get("/admin/newsletters", headers=tokens["boss"]).status_code == 200


def test_write_edit_test_and_send(env):
    client, tokens, ids, Session = env
    boss = tokens["boss"]
    subscribe(Session, ids, "ada")
    made = client.post("/admin/newsletters", json=LETTER, headers=boss).json()
    assert "Three new games" in made["preview_html"] and made["sent_at"] is None
    edited = client.put(f"/admin/newsletters/{made['id']}", json={**LETTER, "subject": "Edited"}, headers=boss).json()
    assert edited["subject"] == "Edited"

    listing = client.get("/admin/newsletters", headers=boss).json()
    assert listing["audience"] == 1 and listing["last_sent_at"] is None

    # A test goes only to the admin, marked as a test.
    assert client.post(f"/admin/newsletters/{made['id']}/test", headers=boss).json() == {"to": "boss@example.com"}
    with Session() as db:
        rows = db.scalars(select(Email)).all()
        assert [r.to_email for r in rows] == ["boss@example.com"]

    sent = client.post(f"/admin/newsletters/{made['id']}/send", json={}, headers=boss).json()
    assert sent["recipients"] == 1 and sent["sent_at"] is not None
    provider = send(Session)
    assert [m.to for m in provider.sent] == ["ada@example.com"]
    assert provider.sent[0].subject == "Edited"
    assert "Three new games" in provider.sent[0].html and "Unsubscribe" in provider.sent[0].html

    # Sent newsletters can't be edited, deleted or sent again.
    assert client.put(f"/admin/newsletters/{made['id']}", json=LETTER, headers=boss).status_code == 409
    assert client.delete(f"/admin/newsletters/{made['id']}", headers=boss).status_code == 409
    assert client.post(f"/admin/newsletters/{made['id']}/send", json={"force": True}, headers=boss).status_code == 409


def test_not_twice_in_two_weeks_unless_forced(env):
    client, tokens, ids, Session = env
    boss = tokens["boss"]
    subscribe(Session, ids)
    first = client.post("/admin/newsletters", json=LETTER, headers=boss).json()
    second = client.post("/admin/newsletters", json={**LETTER, "subject": "Another"}, headers=boss).json()
    assert client.post(f"/admin/newsletters/{first['id']}/send", json={}, headers=boss).status_code == 200
    soon = client.post(f"/admin/newsletters/{second['id']}/send", json={}, headers=boss)
    assert soon.status_code == 409 and "at most every 14 days" in soon.json()["detail"]
    assert client.post(f"/admin/newsletters/{second['id']}/send", json={"force": True}, headers=boss).status_code == 200


def test_after_two_weeks_it_is_fine(env, monkeypatch):
    client, tokens, ids, Session = env
    boss = tokens["boss"]
    subscribe(Session, ids)
    first = client.post("/admin/newsletters", json=LETTER, headers=boss).json()
    client.post(f"/admin/newsletters/{first['id']}/send", json={}, headers=boss)
    real = service.now
    monkeypatch.setattr(service, "now", lambda: real() + timedelta(days=15))
    second = client.post("/admin/newsletters", json={**LETTER, "subject": "Later"}, headers=boss).json()
    assert client.post(f"/admin/newsletters/{second['id']}/send", json={}, headers=boss).status_code == 200


def test_only_confirmed_newsletter_subscribers_get_it(env):
    client, tokens, ids, Session = env
    subscribe(Session, ids, "ada", ["results"])  # not the newsletter topic
    with Session() as db:
        service.ask_to_subscribe(db, "visitor@example.com", ["newsletter"])  # not confirmed yet
    made = client.post("/admin/newsletters", json=LETTER, headers=tokens["boss"]).json()
    sent = client.post(f"/admin/newsletters/{made['id']}/send", json={}, headers=tokens["boss"]).json()
    assert sent["recipients"] == 0


def test_a_subscriber_who_hit_todays_cap_gets_it_tomorrow_not_never(env):
    client, tokens, ids, Session = env
    subscribe(Session, ids, "ada", ["newsletter", "results"])
    service.notify(ids["ada"], "test_result", {"place": 2})
    send(Session)  # today's email to ada has gone out
    made = client.post("/admin/newsletters", json=LETTER, headers=tokens["boss"]).json()
    assert client.post(f"/admin/newsletters/{made['id']}/send", json={}, headers=tokens["boss"]).json()["recipients"] == 1
    assert send(Session).sent == []  # nothing more today
    provider = send(Session, later=timedelta(days=2))
    assert [m.subject for m in provider.sent] == [LETTER["subject"]]
