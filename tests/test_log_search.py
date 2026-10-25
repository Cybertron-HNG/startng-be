from datetime import datetime, timedelta, timezone

from app.models import Activity
from tests.test_admin import env  # noqa: F401  (admins, a super admin "boss", and a test database)

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def seed(Session, rows):
    with Session() as db:
        db.query(Activity).delete()
        for i, (actor, kind, action, target, detail, days_ago) in enumerate(rows):
            db.add(Activity(actor=actor, kind=kind, action=action, target=target, detail=detail, at=NOW - timedelta(days=days_ago, minutes=i)))
        db.commit()


ROWS = [
    ("ada@example.com", "player", "account.created", "ada@example.com", {"via": "google"}, 9),
    ("dev@example.com", "developer", "game.published", "moon-garden", {"repo": "https://github.com/dev/moon"}, 8),
    ("boss@example.com", "admin", "report.approved", "moon-garden", {"reason": "copycat"}, 5),
    ("boss@example.com", "admin", "feature.on", "wardrobe", None, 3),
    (None, "system", "game.updated", "lane-dash", {"commit": "abc123"}, 1),
    ("ada@example.com", "player", "report.created", "lane-dash", {"reason": "100%_broken"}, 0),
]


def search(client, tokens, **params):
    res = client.get("/admin/log/search", params=params, headers=tokens["boss"])
    assert res.status_code == 200, res.text
    return res.json()


def names(page):
    return [(e["action"], e["target"]) for e in page["items"]]


def test_only_admins_with_the_log_permission_can_search(env):
    client, tokens, ids, Session, _ = env
    assert client.get("/admin/log/search").status_code == 401
    assert client.get("/admin/log/search", headers=tokens["player"]).status_code == 404  # not an admin
    mod = client.post("/admin/roles", json={"name": "Mod", "permissions": ["reports.view"]}, headers=tokens["boss"]).json()
    client.post("/admin/admins", json={"email": "mod@example.com", "role_id": mod["id"]}, headers=tokens["boss"])
    assert client.get("/admin/log/search", headers=tokens["mod"]).status_code == 403  # an admin without log.view
    assert client.get("/admin/log/actions", headers=tokens["mod"]).status_code == 403


def test_text_search_looks_in_the_actor_action_target_and_details(env):
    client, tokens, ids, Session, _ = env
    seed(Session, ROWS)
    assert [a for a, _ in names(search(client, tokens, q="moon"))] == ["report.approved", "game.published"]  # target and details
    assert names(search(client, tokens, q="COPYCAT")) == [("report.approved", "moon-garden")]  # in the details, any case
    assert [a for a, _ in names(search(client, tokens, q="ada@"))] == ["report.created", "account.created"]  # the actor
    assert names(search(client, tokens, q="feature")) == [("feature.on", "wardrobe")]  # the action
    assert search(client, tokens, q="no such thing")["total"] == 0


def test_a_percent_or_underscore_in_the_search_is_just_text(env):
    client, tokens, ids, Session, _ = env
    seed(Session, ROWS)
    assert names(search(client, tokens, q="100%_")) == [("report.created", "lane-dash")]
    assert search(client, tokens, q="%")["total"] == 1  # only the entry that really has a percent sign


def test_filters_narrow_the_search_and_combine(env):
    client, tokens, ids, Session, _ = env
    seed(Session, ROWS)
    assert search(client, tokens, kind="admin")["total"] == 2
    assert names(search(client, tokens, action="game.updated")) == [("game.updated", "lane-dash")]
    assert [a for a, _ in names(search(client, tokens, action="report."))] == ["report.created", "report.approved"]  # a prefix
    assert search(client, tokens, action="eport.")["total"] == 0  # a prefix means from the start
    assert search(client, tokens, actor="BOSS")["total"] == 2
    assert names(search(client, tokens, kind="admin", q="wardrobe")) == [("feature.on", "wardrobe")]
    assert search(client, tokens, kind="admin", q="lane-dash")["total"] == 0


def test_date_range_is_inclusive_of_both_days(env):
    client, tokens, ids, Session, _ = env
    seed(Session, ROWS)
    day = lambda d: (NOW - timedelta(days=d)).date().isoformat()  # noqa: E731
    assert search(client, tokens, since=day(5), until=day(3))["total"] == 2  # report.approved and feature.on
    assert search(client, tokens, since=day(1))["total"] == 2
    assert search(client, tokens, until=day(9))["total"] == 1


def test_pages_have_numbers_and_the_newest_come_first(env):
    client, tokens, ids, Session, _ = env
    seed(Session, [("ada@example.com", "player", "x.did", f"t{i}", None, 0) for i in range(25)])
    first = search(client, tokens, page_size=10)
    assert (first["total"], first["pages"], first["page"], len(first["items"])) == (25, 3, 1, 10)
    assert [e["target"] for e in first["items"]][0] == "t24"  # the last one added is the newest
    last = search(client, tokens, page_size=10, page=3)
    assert len(last["items"]) == 5 and last["page"] == 3
    ids_seen = [e["id"] for p in (1, 2, 3) for e in search(client, tokens, page_size=10, page=p)["items"]]
    assert ids_seen == sorted(ids_seen, reverse=True) and len(set(ids_seen)) == 25  # no repeats, none missing
    assert search(client, tokens, page_size=10, page=99)["page"] == 3  # past the end: the last page
    assert search(client, tokens, q="nothing")["pages"] == 1  # an empty result is still one page


def test_the_list_of_actions(env):
    client, tokens, ids, Session, _ = env
    seed(Session, ROWS)
    assert client.get("/admin/log/actions", headers=tokens["boss"]).json() == sorted({r[2] for r in ROWS})


def test_bad_filters_are_refused(env):
    client, tokens, ids, Session, _ = env
    assert client.get("/admin/log/search", params={"kind": "robot"}, headers=tokens["boss"]).status_code == 422
    assert client.get("/admin/log/search", params={"page_size": 1000}, headers=tokens["boss"]).status_code == 422
    assert client.get("/admin/log/search", params={"since": "yesterday"}, headers=tokens["boss"]).status_code == 422
