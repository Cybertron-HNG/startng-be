import json
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base, get_db
from app.features.game_pages import facts as F
from app.features.game_pages import generate as gen
from app.features.game_pages import service
from app.features.game_pages.models import GamePage
from app.main import app
from app.models import Game, User
from app.security import create_token

ROUTER = sys.modules["app.features.game_pages.router"]  # not the package attribute: that name is the APIRouter
SHELL = '<!doctype html><html><head><!--seo--><title>x</title><!--/seo--></head><body><div id="root"></div></body></html>'


class FakeStore:
    def __init__(self, games: dict[str, dict[str, str]]):
        self.games = games

    def paths(self, game):
        return list(self.games.get(game, {}))

    def read_file(self, game, path):
        v = self.games.get(game, {}).get(path)
        return v.encode() if v is not None else None


RICH = {
    "startng.json": json.dumps({"description": "A zombie survival game set in Lagos. It begins at 8:11 a.m.", "controls": ["WASD to move", "Click to shoot"],
                                "features": ["Five Lagos streets", "A day and night cycle"], "walkthrough": ["Find the bus", "Clear the market", "Reach the bridge"]}),
    "index.html": "<html><head><title>Lagos Apocalypto</title></head><body><h1>Lagos Apocalypto</h1></body></html>",
}
BARE = {"index.html": "<html><head><title>Bare</title></head><body><canvas></canvas><script>var x = 'Press space to jump';</script></body></html>"}


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

    monkeypatch.setattr(settings, "features", "game_pages")
    monkeypatch.setattr(settings, "super_admin_emails", "owner@example.com")
    monkeypatch.setattr(ROUTER, "_get_shell", lambda: SHELL)
    app.dependency_overrides[get_db] = override_db
    with Session() as db:
        owner = User(email="owner@example.com", name="Owner", google_sub="g-owner")
        ada = User(email="ada@example.com", name="Ada", google_sub="g-ada")
        db.add_all([owner, ada])
        db.flush()
        db.add_all([Game(name="lagos-apocalypto", owner_id=ada.id, repo_url="x", leaderboard=[{"key": "score", "label": "Score"}, {"key": "time", "label": "Time"}]),
                    Game(name="bare", owner_id=ada.id, repo_url="y")])
        db.commit()
        tokens = {"owner": {"Authorization": f"Bearer {create_token(owner.id)}"}, "ada": {"Authorization": f"Bearer {create_token(ada.id)}"}}
        owner_id = owner.id
    store = FakeStore({"lagos-apocalypto": RICH, "bare": BARE})
    monkeypatch.setattr(ROUTER, "get_game_store", lambda: store)
    yield TestClient(app), tokens, Session, store, owner_id
    app.dependency_overrides.clear()


# ---- facts: only what the files say

def test_facts_from_the_creators_own_manifest():
    f = F.Facts(title="T")
    F.from_manifest(RICH["startng.json"].encode(), f)
    assert f.description.startswith("A zombie survival") and f.controls == ["WASD to move", "Click to shoot"] and f.sources["controls"] == "startng.json"
    assert len(f.walkthrough) == 3 and len(f.features) == 2


def test_a_broken_or_huge_manifest_is_ignored():
    f = F.Facts(title="T")
    F.from_manifest(b"{not json", f)
    F.from_manifest(b"[1,2]", f)
    F.from_manifest(b"x" * 70000, f)
    assert f.description is None and f.controls == []


def test_facts_from_a_readme_sections():
    readme = "# Moon Run\n\nA calm night runner through a sleeping city.\n\n## How to play\n1. Tap to jump\n2. Hold to glide\n\n## Features\n- Ten levels\n- Daily goals\n\n## Tips\n- Jump early\n- Glide late\n- Keep moving\n"
    f = F.Facts(title="T")
    F.from_readme(readme.encode(), f)
    assert f.steps == ["Tap to jump", "Hold to glide"] and f.features == ["Ten levels", "Daily goals"] and len(f.walkthrough) == 3
    assert f.description.startswith("A calm night runner") and f.sources["description"] == "README"


def test_the_manifest_beats_the_readme():
    f = F.Facts(title="T")
    F.from_manifest(json.dumps({"controls": ["From the manifest"]}).encode(), f)
    F.from_readme(b"## Controls\n- From the readme\n", f)
    assert f.controls == ["From the manifest"]


def test_facts_from_the_games_own_page_skip_scripts_and_find_the_how_to_play_words():
    page = "<html><head><title>X</title><meta name='description' content='A tiny puzzle about moving crates around a harbour.'></head><body><h2>How to play</h2><p>Use the arrow keys to push crates.</p><p>Press R to restart.</p><script>var s='Click here to win a prize'</script></body></html>"
    f = F.Facts(title="T")
    F.from_page(page.encode(), f)
    assert f.description.startswith("A tiny puzzle") and f.controls == ["Use the arrow keys to push crates.", "Press R to restart."]
    assert not any("prize" in c for c in f.controls)  # words inside a script are not shown to anyone


def test_key_names_in_inline_tags_stay_in_their_sentence():
    # oven-rush and flood-rise write their controls like this; the kbd and b words used to be dropped (Ziki #89)
    page = ("<html><body><h2>How to play</h2>"
            "<p>Tap a <b>shelf</b> to pick up a cookie or boba (or press <kbd>1</kbd>–<kbd>6</kbd>). The tray holds 3.</p>"
            "<ul><li><kbd>W A S D</kbd> or arrows to run</li><li><kbd>Space</kbd> to jump (hold for higher)</li></ul>"
            "<p>Press <a href='/help'>help</a> any time.</p></body></html>")
    f = F.Facts(title="T")
    F.from_page(page.encode(), f)
    assert f.controls[:3] == ["Tap a shelf to pick up a cookie or boba (or press 1–6). The tray holds 3.",
                              "W A S D or arrows to run", "Space to jump (hold for higher)"]
    assert "help" not in f.controls  # a link is still its own block, as before


def test_text_is_cleaned_of_markup_and_control_characters():
    assert F.clean("**Bold** [a link](http://x.example) <b>tag</b>\x00 end") == "Bold a link tag end"


# ---- a page is only written when the facts are enough

def test_a_game_with_no_words_gets_no_pages_and_a_reason():
    f = F.Facts(title="Bare")
    for kind in gen.KINDS:
        out = gen.build(kind, f, "bare")
        assert isinstance(out, str) and out  # the reason


def test_a_rich_game_gets_all_four_pages_from_its_facts_only():
    f = F.read_facts(FakeStore({"lagos-apocalypto": RICH}), type("G", (), {"name": "lagos-apocalypto", "description": None, "leaderboard": [{"key": "score", "label": "Score"}], "owner": None})(), ["Nigerian themed"])
    drafts = {k: gen.build(k, f, "lagos-apocalypto") for k in gen.KINDS}
    assert all(isinstance(d, gen.Draft) for d in drafts.values())
    assert "WASD to move" in drafts["how-to-play"].body and "Five Lagos streets" in drafts["about"].body
    assert "1. Find the bus" in drafts["walkthrough"].body and "nigerian themed" in drafts["about"].body.lower()
    assert "Is Lagos Apocalypto free to play?" in drafts["faq"].body
    assert all(len(d.description) <= 160 for d in drafts.values())


def test_markdown_is_escaped_and_faq_pairs_are_read_back():
    html = gen.to_html("## Q <b>\n\nA & B <script>alert(1)</script>\n\n- one\n- two\n\n1. first\n2. second")
    assert "<script>" not in html and "&lt;script&gt;" in html and "<ul>" in html and "<ol>" in html
    assert gen.faq_pairs("## Q1\n\nA1 line one\nline two\n\n## Q2\n\nA2") == [("Q1", "A1 line one line two"), ("Q2", "A2")]
    assert gen.ordered_steps("1. a\n2. b") == ["a", "b"]


# ---- making drafts, and keeping what a person did

def test_generate_makes_drafts_for_rich_games_and_skips_bare_ones(env):
    client, tokens, Session, store, _ = env
    with Session() as db:
        counts = service.generate_all(db, store)
        rows = {(p.game_name, p.kind): p for p in db.query(GamePage).all()}
    assert rows[("lagos-apocalypto", "faq")].status == "draft" and rows[("bare", "about")].status == "skipped"
    assert "description" in rows[("bare", "about")].reason.lower() and counts["draft"] == 4 and counts["skipped"] == 4


def test_regenerating_keeps_approved_and_edited_pages_unless_forced(env):
    client, tokens, Session, store, owner_id = env
    with Session() as db:
        service.generate_all(db, store)
        about = db.query(GamePage).filter_by(game_name="lagos-apocalypto", kind="about").one()
        faq = db.query(GamePage).filter_by(game_name="lagos-apocalypto", kind="faq").one()
        service.approve(db, about.id, owner_id)
        service.set_text(db, faq.id, "My title", "My description", "## Mine\n\nMy words")
        service.generate_all(db, store)
        assert db.query(GamePage).filter_by(game_name="lagos-apocalypto", kind="about").one().status == "approved"
        assert db.query(GamePage).filter_by(game_name="lagos-apocalypto", kind="faq").one().body == "## Mine\n\nMy words"
        service.generate_all(db, store, force=True)
        assert db.query(GamePage).filter_by(game_name="lagos-apocalypto", kind="faq").one().body != "## Mine\n\nMy words"


def test_two_games_with_the_same_words_get_one_page_not_two(env):
    client, tokens, Session, store, _ = env
    with Session() as db:
        owner = db.query(User).first()
        db.add(Game(name="lagos-apocalypto-copy", owner_id=owner.id, repo_url="z", leaderboard=[{"key": "score", "label": "Score"}, {"key": "time", "label": "Time"}]))
        db.commit()
    store.games["lagos-apocalypto-copy"] = RICH
    with Session() as db:
        service.generate_all(db, store)
        copy = db.query(GamePage).filter_by(game_name="lagos-apocalypto-copy", kind="how-to-play").one()
        assert copy.status == "skipped" and "Too much like" in copy.reason


# ---- the public page is the server's HTML, and only approved pages are public

def _approve(client, tokens, Session, store, game="lagos-apocalypto", kind="faq"):
    assert client.post("/admin/game-pages/generate", json={}, headers=tokens["owner"]).status_code == 200
    rows = client.get("/admin/game-pages?status=draft", headers=tokens["owner"]).json()
    page = next(r for r in rows if r["game"] == game and r["kind"] == kind)
    assert client.post(f"/admin/game-pages/{page['id']}/approve", headers=tokens["owner"]).status_code == 200
    return page["id"]


def test_a_draft_is_not_public_an_approved_page_is(env):
    client, tokens, Session, store, _ = env
    client.post("/admin/game-pages/generate", json={}, headers=tokens["owner"])
    r = client.get("/pages/games/lagos-apocalypto/faq")
    assert r.status_code == 404 and "noindex" in r.text  # still a draft
    _approve(client, tokens, Session, store)
    r = client.get("/pages/games/lagos-apocalypto/faq")
    assert r.status_code == 200
    assert r.text.count("<h1>") == 1 and "<h1>Lagos Apocalypto FAQ</h1>" in r.text
    assert '<link rel="canonical" href="' in r.text and "/games/lagos-apocalypto/faq" in r.text
    assert '"@type": "FAQPage"' in r.text and '"@type": "BreadcrumbList"' in r.text
    assert "Play Lagos Apocalypto" in r.text and "<title>Lagos Apocalypto FAQ · start.ng</title>" in r.text


def test_the_page_lists_its_siblings_and_the_games_pages_endpoint_matches(env):
    client, tokens, Session, store, _ = env
    _approve(client, tokens, Session, store, kind="faq")
    rows = client.get("/admin/game-pages?status=draft", headers=tokens["owner"]).json()
    about = next(r for r in rows if r["game"] == "lagos-apocalypto" and r["kind"] == "about")
    client.post(f"/admin/game-pages/{about['id']}/approve", headers=tokens["owner"])
    assert 'href="/games/lagos-apocalypto/about"' in client.get("/pages/games/lagos-apocalypto/faq").text
    pages = client.get("/games/lagos-apocalypto/pages").json()
    assert [p["kind"] for p in pages] == ["about", "faq"] and pages[1]["path"] == "/games/lagos-apocalypto/faq"


def test_hidden_games_and_unknown_pages_are_404_and_noindex(env):
    client, tokens, Session, store, _ = env
    _approve(client, tokens, Session, store)
    with Session() as db:
        db.get(Game, "lagos-apocalypto").hidden = "admin"
        db.commit()
    r = client.get("/pages/games/lagos-apocalypto/faq")
    assert r.status_code == 404 and "noindex" in r.text
    assert client.get("/pages/games/nope/about").status_code == 404
    assert client.get("/pages/games/lagos-apocalypto/secrets").status_code == 422  # not one of the four kinds
    assert client.get("/games/lagos-apocalypto/pages").json() == []


def test_the_page_is_503_when_the_website_shell_is_down(env, monkeypatch):
    client, tokens, Session, store, _ = env
    monkeypatch.setattr(ROUTER, "_get_shell", lambda: None)
    assert client.get("/pages/games/lagos-apocalypto/faq").status_code == 503


def test_edited_text_is_escaped_on_the_public_page(env):
    client, tokens, Session, store, _ = env
    pid = _approve(client, tokens, Session, store)
    r = client.put(f"/admin/game-pages/{pid}", json={"title": "FAQ <img src=x>", "description": "d", "body": "## Q\n\n<script>alert(1)</script>"}, headers=tokens["owner"])
    assert r.status_code == 200 and r.json()["status"] == "draft"  # changed text needs a new approval
    client.post(f"/admin/game-pages/{pid}/approve", headers=tokens["owner"])
    html = client.get("/pages/games/lagos-apocalypto/faq").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html and "<img src=x>" not in html


# ---- admin

def test_only_admins_can_use_the_admin_side(env):
    client, tokens, Session, store, _ = env
    for method, path in (("get", "/admin/game-pages"), ("get", "/admin/game-pages/gaps"), ("post", "/admin/game-pages/generate")):
        assert getattr(client, method)(path, headers=tokens["ada"], **({"json": {}} if method == "post" else {})).status_code == 404


def test_skipped_pages_cant_be_edited_or_approved_and_the_gaps_list_says_why(env):
    client, tokens, Session, store, _ = env
    client.post("/admin/game-pages/generate", json={}, headers=tokens["owner"])
    skipped = client.get("/admin/game-pages?status=skipped", headers=tokens["owner"]).json()
    assert {r["game"] for r in skipped} == {"bare"}
    pid = skipped[0]["id"]
    assert client.post(f"/admin/game-pages/{pid}/approve", headers=tokens["owner"]).status_code == 409
    assert client.put(f"/admin/game-pages/{pid}", json={"title": "t", "description": "", "body": "b"}, headers=tokens["owner"]).status_code == 409
    gaps = client.get("/admin/game-pages/gaps", headers=tokens["owner"]).json()
    assert [g["game"] for g in gaps] == ["bare"] and any("description" in r.lower() for r in gaps[0]["reasons"])
    assert client.get("/admin/game-pages/summary", headers=tokens["owner"]).json()["games_without_pages"] == 1


def test_generating_without_game_files_is_refused_politely(env, monkeypatch):
    client, tokens, Session, store, _ = env
    monkeypatch.setattr(ROUTER, "get_game_store", lambda: None)
    assert client.post("/admin/game-pages/generate", json={}, headers=tokens["owner"]).status_code == 503


def test_tags_and_a_leaderboard_alone_do_not_make_a_faq():
    f = F.Facts(title="Bare", tags=["Nigerian themed", "Lagos"], leaderboard=["Score"])
    out = gen.build("faq", f, "bare")
    assert isinstance(out, str) and "description" in out.lower()
    f.description = "A short runner through a quiet market in Lagos."
    assert isinstance(gen.build("faq", f, "bare"), gen.Draft)


def test_the_faq_answer_uses_the_whole_description_when_it_is_short():
    f = F.Facts(title="Lagos Apocalypto", description="A zombie survival game set in Lagos. It begins at 8:11 a.m.", controls=["WASD to move"])
    body = gen.build("faq", f, "lagos-apocalypto").body
    assert "A zombie survival game set in Lagos. It begins at 8:11 a.m." in body


def test_the_sitemap_lists_approved_pages_only(env):
    client, tokens, Session, store, _ = env
    _approve(client, tokens, Session, store, kind="faq")
    xml = client.get("/pages/sitemap.xml").text
    assert "/games/lagos-apocalypto/faq</loc>" in xml and "<lastmod>" in xml.split("/games/lagos-apocalypto/faq</loc>")[1][:40]
    assert "/games/lagos-apocalypto/about</loc>" not in xml  # still a draft
    with Session() as db:
        db.get(Game, "lagos-apocalypto").hidden = "admin"
        db.commit()
    assert "/games/lagos-apocalypto/faq</loc>" not in client.get("/pages/sitemap.xml").text  # hidden games are not listed


def test_the_sitemap_lists_no_pages_while_the_flag_is_off(env, monkeypatch):
    client, tokens, Session, store, _ = env
    _approve(client, tokens, Session, store, kind="faq")
    monkeypatch.setattr(settings, "features", "")
    assert "/games/lagos-apocalypto/faq" not in client.get("/pages/sitemap.xml").text


def test_the_games_server_page_and_its_own_pages_link_to_each_other_and_to_categories(env, monkeypatch):
    client, tokens, Session, store, _ = env
    monkeypatch.setattr("app.routers.pages._get_shell", lambda: SHELL)
    monkeypatch.setattr(settings, "features", "game_pages,tags")
    from app.features.tags import service as tag_service
    with Session() as db:
        owner = db.query(User).first()
        db.add_all([Game(name=f"extra-{i}", owner_id=owner.id, repo_url="z") for i in range(2)])
        db.commit()
        for n in ("lagos-apocalypto", "extra-0", "extra-1"):
            tag_service.approve(db, n, ["nigerian-themed"])
    _approve(client, tokens, Session, store, kind="faq")
    game = client.get("/pages/games/lagos-apocalypto").text
    assert 'aria-label="More about this game"' in game and 'href="/games/lagos-apocalypto/faq"' in game and "Lagos Apocalypto FAQ" in game
    assert 'aria-label="Categories"' in game and 'href="/categories/nigerian-themed"' in game
    sub = client.get("/pages/games/lagos-apocalypto/faq").text
    assert 'aria-label="Categories"' in sub and 'href="/categories/nigerian-themed"' in sub
    assert "More about this game" not in client.get("/pages/games/bare").text  # nothing approved for it


def test_a_game_page_has_no_links_to_pages_while_the_flag_is_off(env, monkeypatch):
    client, tokens, Session, store, _ = env
    monkeypatch.setattr("app.routers.pages._get_shell", lambda: SHELL)
    _approve(client, tokens, Session, store, kind="faq")
    monkeypatch.setattr(settings, "features", "")
    assert "More about this game" not in client.get("/pages/games/lagos-apocalypto").text


# ---- the pages say more than the creator's own words: only facts we really have

BOARD = [{"key": "stars", "label": "Stars", "order": "highest", "format": "number"}, {"key": "time", "label": "Time", "order": "lowest", "format": "time"}]


def _facts(**over):
    base = dict(title="Star Hop", creator="Ada", description="Hop between stars before the sky runs out of them.", controls=["Tap to hop", "Hold to glide"],
                tags=["Endless runners"], published="March 2026", board=BOARD, leaderboard=["Stars", "Time"])
    return F.Facts(**{**base, **over})


def test_scoring_is_explained_from_the_games_own_columns():
    text = gen.ranking(_facts())
    assert text.startswith("Players are ranked by Stars (highest first).")
    assert "If two players tie, Time (fastest first) decides it." in text
    assert "Only your best run counts" in text
    assert gen.ranking(_facts(board=[], leaderboard=[])) == ""  # no leaderboard, nothing to say


def test_the_about_page_has_the_facts_at_a_glance():
    body = gen.build("about", _facts(features=["Ten levels"]), "star-hop").body
    for line in ("## Star Hop at a glance", "- Made by Ada", "- On start.ng since March 2026", "- Play it in your browser at star-hop.start.ng, with nothing to download",
                 "- Categories: Endless runners", "- Leaderboard: yes, ranked by Stars and Time", "- Controls: Tap to hop", "## How scoring works"):
        assert line in body, line


def test_the_about_page_leaves_out_what_it_doesnt_know():
    body = gen.build("about", _facts(board=[], leaderboard=[], tags=[], published=None, controls=[]), "star-hop").body
    assert "at a glance" in body and "Made by Ada" in body
    for missing in ("On start.ng since", "Categories:", "Leaderboard:", "Controls:", "How scoring works"):
        assert missing not in body, missing


def test_how_to_play_has_a_goal_scoring_and_what_to_do_first():
    body = gen.build("how-to-play", _facts(steps=["Hop to the next star", "Do not fall"], walkthrough=["a", "b", "c"]), "star-hop").body
    for part in ("## The goal", "Hop between stars before the sky runs out of them.", "## Controls", "## How to play", "## Scoring and the leaderboard",
                 "## Before you start", "Sign in with start.ng first", "Stuck? Star Hop has a walkthrough"):
        assert part in body, part
    plain = gen.build("how-to-play", _facts(board=[], leaderboard=[], walkthrough=[]), "star-hop").body
    assert "Sign in with start.ng first" not in plain and "Stuck?" not in plain and "Scoring and the leaderboard" not in plain


def test_the_faq_says_since_when_and_how_the_board_ranks():
    pairs = dict(gen.faq_pairs(gen.build("faq", _facts(), "star-hop").body))
    assert pairs["Who made Star Hop?"] == "Star Hop was made by Ada. It has been on start.ng since March 2026."
    assert "Players are ranked by Stars (highest first)." in pairs["Does Star Hop have a leaderboard?"]


def test_nothing_is_said_that_the_facts_dont_support():
    for kind in ("about", "how-to-play", "faq"):
        body = gen.build(kind, _facts(), "star-hop").body.lower()
        for claim in ("multiplayer", "phone", "mobile", "offline", "best game", "most popular", "fun"):
            assert claim not in body, (kind, claim)


# ---- live blocks on a game's pages: the top players, more by the same creator, similar games

def _approved_about(client, tokens, Session, store, game="lagos-apocalypto"):
    return _approve(client, tokens, Session, store, game=game, kind="about")


def test_top_players_are_read_live_not_stored(env):
    client, tokens, Session, store, _ = env
    from app.models import Score
    with Session() as db:
        db.get(Game, "lagos-apocalypto").leaderboard = [{"key": "score", "label": "Score", "order": "highest", "format": "number"},
                                                        {"key": "time", "label": "Time", "order": "lowest", "format": "time"}]
        ada = db.query(User).filter_by(email="ada@example.com").one()
        tunde = User(email="tunde@example.com", name="Tunde", google_sub="g-tunde")
        db.add(tunde)
        db.flush()
        db.add_all([Score(user_id=ada.id, game_name="lagos-apocalypto", best=40, stats={"score": 40, "time": 125}),
                    Score(user_id=tunde.id, game_name="lagos-apocalypto", best=55, stats={"score": 55, "time": 90})])
        db.commit()
    _approved_about(client, tokens, Session, store)
    page = client.get("/pages/games/lagos-apocalypto/about").text
    assert 'aria-label="Top players"' in page and "Top players right now" in page
    block = page[page.index("Top players right now"):]
    assert block.index("Tunde") < block.index("Ada")  # the better score first
    assert "Score 55" in block and "Time 1:30" in block  # a time is shown as m:ss
    assert 'href="/games/lagos-apocalypto"' in page
    # it moves with the scores: nothing was stored in the page
    with Session() as db:
        db.query(Score).filter_by(game_name="lagos-apocalypto").delete()
        db.commit()
    assert "Top players right now" not in client.get("/pages/games/lagos-apocalypto/about").text


def test_more_games_by_the_same_creator_are_linked_with_their_cards(env):
    client, tokens, Session, store, _ = env
    with Session() as db:
        ada = db.query(User).filter_by(email="ada@example.com").one()
        db.add(Game(name="moon-run", owner_id=ada.id, repo_url="m"))
        db.add(Game(name="hidden-one", owner_id=ada.id, repo_url="h", hidden="blocked"))
        other = User(email="x@example.com", name="Xavier", google_sub="g-x")
        db.add(other)
        db.flush()
        db.add(Game(name="not-ada", owner_id=other.id, repo_url="n"))
        db.commit()
    _approved_about(client, tokens, Session, store)
    page = client.get("/pages/games/lagos-apocalypto/about").text
    assert "More games by Ada" in page
    assert 'href="/games/moon-run"' in page and 'href="/games/bare"' in page
    assert "not-ada" not in page and "hidden-one" not in page  # not someone else's, not a hidden one
    assert page.count('href="/games/lagos-apocalypto"') >= 1 and 'class="game-link" href="/games/lagos-apocalypto"' not in page  # not itself


def test_games_in_the_same_category_are_linked_when_tags_is_on(env, monkeypatch):
    client, tokens, Session, store, _ = env
    from app.features.tags import service as tags
    monkeypatch.setattr(settings, "features", "game_pages,tags")
    with Session() as db:
        owner = db.query(User).filter_by(email="owner@example.com").one()
        for n in ("dash-one", "dash-two", "dash-three"):
            db.add(Game(name=n, owner_id=owner.id, repo_url=n))
        db.commit()
        for n in ("lagos-apocalypto", "dash-one", "dash-two", "dash-three"):
            tags.approve(db, n, ["endless-runners"])
    _approved_about(client, tokens, Session, store)
    page = client.get("/pages/games/lagos-apocalypto/about").text
    assert "Games like Lagos Apocalypto" in page
    for n in ("dash-one", "dash-two", "dash-three"):
        assert f'class="game-link" href="/games/{n}"' in page
    assert 'class="game-link" href="/games/lagos-apocalypto"' not in page  # not itself
    monkeypatch.setattr(settings, "features", "game_pages")  # tags off: no "games like this", the page still works
    assert "Games like" not in client.get("/pages/games/lagos-apocalypto/about").text


# ---- more of the game's own words, and a warning when there is nothing to say

def test_more_of_the_readme_and_the_games_own_page_is_used():
    f = F.Facts(title="Moon Run")
    readme = (b"# Moon Run\n\nA calm night runner through a sleeping city, where every rooftop is a new ledge to cross.\n\n"
              b"## Why we made it\nWe wanted a game you can play slowly, at the end of a long day, with nothing chasing you.\n\n"
              b"## How to play\n1. Tap to jump\n\n## Features\n- Ten levels\n")
    F.from_readme(readme, f)
    assert f.description == "A calm night runner through a sleeping city, where every rooftop is a new ledge to cross."
    assert f.about_more == ["We wanted a game you can play slowly, at the end of a long day, with nothing chasing you."]
    page = b"<html><body><p>The city sleeps, but the rooftops are awake. Follow the lantern light and do not wake anyone up.</p><p>Tap to jump</p></body></html>"
    F.from_page(page, f)
    assert "The city sleeps, but the rooftops are awake. Follow the lantern light and do not wake anyone up." in f.about_more
    assert "Tap to jump" not in f.about_more  # a control, not a story


def test_the_about_page_tells_what_the_game_is_about_in_the_games_own_words():
    f = F.Facts(title="Moon Run", creator="Ada", description="A calm night runner.", about_more=["The city sleeps, but the rooftops are awake."], features=["Ten levels"])
    body = gen.build("about", f, "moon-run").body
    assert body.startswith("## What is Moon Run about?")
    assert "A calm night runner." in body and "The city sleeps, but the rooftops are awake." in body


# ---- drafts are made on their own (a person still approves)

def test_games_without_pages_get_drafts_a_few_at_a_time(env):
    client, tokens, Session, store, _ = env
    with Session() as db:
        ada = db.query(User).filter_by(email="ada@example.com").one()
        db.add(Game(name="secret", owner_id=ada.id, repo_url="s", hidden="blocked"))
        db.commit()
    store.games["secret"] = RICH
    with Session() as db:
        assert service.make_missing_drafts(db, store, limit=1) == 1
        assert db.query(GamePage.game_name).distinct().count() == 1
        assert service.make_missing_drafts(db, store, limit=5) == 1  # the other visible game
        assert service.make_missing_drafts(db, store, limit=5) == 0  # nothing left to do
        assert db.query(GamePage).filter_by(game_name="secret").count() == 0  # a hidden game gets nothing
        assert {p.status for p in db.query(GamePage).all()} <= {"draft", "skipped"}  # never approved by itself
        assert service.make_missing_drafts(db, None) == 0  # the games' files aren't reachable: nothing happens


def test_a_new_version_remakes_drafts_but_never_an_approved_or_edited_page(env, monkeypatch):
    client, tokens, Session, store, _ = env
    from app import events
    monkeypatch.setattr(service, "SessionLocal", Session)
    monkeypatch.setattr(service, "get_game_store", lambda: store)
    with Session() as db:
        service.generate_for(db, store, db.get(Game, "lagos-apocalypto"))
    pid = _approve(client, tokens, Session, store)  # approves the FAQ
    store.games["lagos-apocalypto"] = {**RICH, "startng.json": json.dumps({"description": "A new description for a new version.", "controls": ["Press X to dash"]})}
    events.publish("game_updated", {"game": "lagos-apocalypto", "owner_id": 1, "auto": True})  # the feature is subscribed to it
    with Session() as db:
        faq = db.get(GamePage, pid)
        assert faq.status == "approved" and "A new description" not in faq.body  # the approved page stays as a person left it
        how = db.query(GamePage).filter_by(game_name="lagos-apocalypto", kind="how-to-play").one()
        assert how.status == "draft" and "Press X to dash" in how.body  # a draft follows the new version


def test_nothing_is_made_on_its_own_while_the_feature_is_off(env, monkeypatch):
    client, tokens, Session, store, _ = env
    monkeypatch.setattr(service, "SessionLocal", Session)
    monkeypatch.setattr(service, "get_game_store", lambda: store)
    monkeypatch.setattr(settings, "features", "")
    service.on_game_updated({"game": "lagos-apocalypto"})
    with Session() as db:
        assert db.query(GamePage).count() == 0


# ---- what the game's own source says (app/brief.py), used on its pages

BRIEF = {
    "v": 1,
    "readme": "# Relic Hunter\n\nA temple escape: grab the idol, then outrun a rolling boulder.\n\n## Features\n- Ten rooms\n",
    "messages": [
        "RUN! Escape the boulder - get back through the vault door",
        "The vault is open. Take the golden idol",
        "Climb the ledge and pull the lever to open the gate",
        "You escaped with the idol!",
        "You fell into the darkness",
    ],
    "notes": ["Relic Hunter: a temple escape with a rolling boulder behind you."],
    "names": ["tomb", "boulder"],
    "files": 9,
}


def test_the_brief_splits_instructions_from_other_lines_and_fills_in_a_missing_readme():
    f = F.Facts(title="Relic Hunter")
    F.from_brief(BRIEF, f, readme_found=False)
    assert f.hints == ["RUN! Escape the boulder - get back through the vault door", "Climb the ledge and pull the lever to open the gate"]
    assert f.messages == ["The vault is open. Take the golden idol", "You escaped with the idol!", "You fell into the darkness"]
    assert f.description == "A temple escape: grab the idol, then outrun a rolling boulder."  # from the repo's README: the game's files had none
    assert f.features == ["Ten rooms"] and f.notes == ["Relic Hunter: a temple escape with a rolling boulder behind you."]
    assert f.sources["messages"] == "the game's source"
    kept = F.Facts(title="Relic Hunter")
    F.from_brief(BRIEF, kept, readme_found=True)  # the game's own README wins: the repo's is not read again
    assert kept.description is None and kept.hints


def test_a_missing_or_odd_brief_changes_nothing():
    for odd in (None, {}, "x", {"messages": [1, None, ""], "notes": "no"}):
        f = F.Facts(title="X")
        F.from_brief(odd, f, readme_found=False)
        assert f.messages == f.hints == f.notes == [] and f.description is None


def test_the_about_page_quotes_what_the_game_says():
    f = F.Facts(title="Relic Hunter", creator="Ada")
    F.from_brief(BRIEF, f, readme_found=False)
    body = gen.build("about", f, "relic-hunter").body
    assert "## What Relic Hunter says" in body and "Some of what Relic Hunter tells you as you play:" in body
    for line in ("- “The vault is open. Take the golden idol”", "- “You escaped with the idol!”", "- “RUN! Escape the boulder - get back through the vault door”"):
        assert line in body, line
    assert "A temple escape: grab the idol" in body  # the repo's README says what it is


def test_how_to_play_can_stand_on_what_the_game_tells_you_but_not_on_one_line():
    f = F.Facts(title="Relic Hunter")
    F.from_brief(BRIEF, f, readme_found=False)
    made = gen.build("how-to-play", f, "relic-hunter")
    assert not isinstance(made, str)
    assert "## What Relic Hunter tells you to do" in made.body and "- “Climb the ledge and pull the lever to open the gate”" in made.body
    assert "RUN! Escape the boulder" in made.description or "A temple escape" in made.description or made.description  # a meta description exists
    one = F.Facts(title="Relic Hunter", hints=["Climb the ledge and pull the lever to open the gate"])
    assert isinstance(gen.build("how-to-play", one, "relic-hunter"), str)  # skipped: one line is not a page


def test_a_game_with_only_a_brief_still_gets_an_about_page_but_a_page_with_nothing_does_not():
    only = F.Facts(title="X", messages=["One line of the game.", "Another line of the game.", "A third line of the game."])
    assert not isinstance(gen.build("about", only, "x"), str)
    assert isinstance(gen.build("about", F.Facts(title="X", messages=["Just one line."]), "x"), str)


# ---- backfill: games published before the brief existed are read from their repos, a few at a time

def _zip_for_backfill():
    from tests.test_publish import GAME, make_zip
    return make_zip({**GAME, "README.md": "# Moon Run\n\nA calm night runner through a sleeping city.", "src/game.js": 'const a = "RUN! Escape the boulder - get back through the vault door";'})


def test_games_without_a_brief_get_one_from_their_repo(env, monkeypatch):
    client, tokens, Session, store, _ = env
    asked = []
    monkeypatch.setattr(service, "download_repo", lambda repo: asked.append(repo.name) or _zip_for_backfill())
    monkeypatch.setattr(service.auto_update, "paused_until", lambda: None)
    for name in ("lagos-apocalypto", "bare"):
        with Session() as db:
            db.get(Game, name).repo_url = f"https://github.com/ada/{name}"
            db.commit()
    with Session() as db:
        assert set(service.backfill_briefs(db, limit=5)) == {"lagos-apocalypto", "bare"}
        assert "RUN! Escape the boulder - get back through the vault door" in db.get(Game, "lagos-apocalypto").brief["messages"]
        assert sorted(asked) == ["bare", "lagos-apocalypto"]
        assert service.backfill_briefs(db, limit=5) == []  # every game has one now: nothing is asked again


def test_a_link_that_cannot_be_read_does_not_block_the_games_behind_it(env, monkeypatch):
    client, tokens, Session, store, _ = env
    monkeypatch.setattr(service.auto_update, "paused_until", lambda: None)
    monkeypatch.setattr(service, "download_repo", lambda repo: _zip_for_backfill())
    with Session() as db:
        db.get(Game, "bare").repo_url = "not a github link"
        db.get(Game, "lagos-apocalypto").repo_url = "https://github.com/ada/Lagos-Apocalypto"
        db.commit()
        assert service.backfill_briefs(db, limit=5) == ["lagos-apocalypto"]
        assert db.get(Game, "bare").brief["unavailable"] == "bad_link"  # remembered, not retried every round


def test_a_gone_repo_is_marked_and_a_hiccup_is_tried_again_later(env, monkeypatch):
    client, tokens, Session, store, _ = env
    from app.publish import PublishError
    monkeypatch.setattr(service.auto_update, "paused_until", lambda: None)
    for game in ("lagos-apocalypto", "bare"):
        with Session() as db:
            db.get(Game, game).repo_url = f"https://github.com/ada/{game}"
            db.commit()

    def gone(repo):
        raise PublishError("Repo or branch not found", "repo_not_found")

    monkeypatch.setattr(service, "download_repo", gone)
    with Session() as db:
        assert service.backfill_briefs(db, limit=5) == []
        assert db.get(Game, "bare").brief["unavailable"] == "repo_not_found"  # remembered
        assert service.backfill_briefs(db, limit=5) == []  # and not asked for again

    with Session() as db:
        for game in ("lagos-apocalypto", "bare"):
            db.get(Game, game).brief = None
        db.commit()

    def hiccup(repo):
        raise PublishError("Couldn't download", "download_failed")

    monkeypatch.setattr(service, "download_repo", hiccup)
    with Session() as db:
        assert service.backfill_briefs(db, limit=5) == []
        assert db.get(Game, "bare").brief is None  # left for a later round


def test_backfill_waits_while_githubs_limit_is_used_up(env, monkeypatch):
    client, tokens, Session, store, _ = env
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(service.auto_update, "paused_until", lambda: datetime.now(timezone.utc) + timedelta(minutes=10))
    monkeypatch.setattr(service, "download_repo", lambda repo: (_ for _ in ()).throw(AssertionError("must not ask GitHub")))
    with Session() as db:
        assert service.backfill_briefs(db, limit=5) == []


def test_a_game_whose_brief_arrives_gets_its_pages_from_it_end_to_end(env):
    client, tokens, Session, store, _ = env
    with Session() as db:
        game = db.get(Game, "bare")
        game.brief = BRIEF
        db.commit()
        service.generate_for(db, store, game)
        about = db.query(GamePage).filter_by(game_name="bare", kind="about").one()
        assert about.status == "draft" and "“You escaped with the idol!”" in about.body
        assert about.facts["messages"] and about.facts["hints"]  # the reviewer can see what it was written from

# ---- pages approve themselves, with a safety check instead of a person

def test_safety_check():
    assert service.is_safe("About Zap", "A fast game.", "## Story\nRun and jump.")
    assert not service.is_safe("About Zap", "Visit https://evil.example now", "Run.")
    assert not service.is_safe("About Zap", "ok", "Email me @someone <b>hi</b>")
    assert not service.is_safe("About Zap", "ok", "This is shit")
    assert not service.is_safe("", "ok", "Run.")


def _statuses(Session, game="lagos-apocalypto"):
    with Session() as db:
        return {p.kind: p.status for p in db.query(GamePage).filter_by(game_name=game).all()}


def test_good_pages_are_approved_by_themselves(env, monkeypatch):
    _, _, Session, store, _ = env
    monkeypatch.setattr(settings, "game_pages_auto_approve", True)
    with Session() as db:
        service.generate_for(db, store, db.get(Game, "lagos-apocalypto"))
    statuses = _statuses(Session)
    assert statuses["about"] == "approved" and "draft" not in statuses.values()
    with Session() as db:
        assert db.query(GamePage).filter_by(game_name="lagos-apocalypto", status="approved").first().approved_by_id is None


def test_off_leaves_drafts_for_a_person(env):
    _, _, Session, store, _ = env  # off in tests by default
    with Session() as db:
        service.generate_for(db, store, db.get(Game, "lagos-apocalypto"))
    assert "approved" not in _statuses(Session).values() and "draft" in _statuses(Session).values()


def test_unsafe_text_waits_for_a_person(env, monkeypatch):
    _, _, Session, store, _ = env
    monkeypatch.setattr(settings, "game_pages_auto_approve", True)
    bad = dict(RICH)
    bad["startng.json"] = json.dumps({"description": "A game. Visit http://spam.example for more cheats and fun.", "controls": ["WASD to move"]})
    store.games["lagos-apocalypto"] = bad
    with Session() as db:
        service.generate_for(db, store, db.get(Game, "lagos-apocalypto"))
    assert "approved" not in _statuses(Session).values()


def test_a_page_an_admin_took_down_stays_down(env, monkeypatch):
    _, _, Session, store, _ = env
    monkeypatch.setattr(settings, "game_pages_auto_approve", True)
    with Session() as db:
        service.generate_for(db, store, db.get(Game, "lagos-apocalypto"))
        page = db.query(GamePage).filter_by(game_name="lagos-apocalypto", kind="about").one()
        service.unapprove(db, page.id)
        service.generate_for(db, store, db.get(Game, "lagos-apocalypto"))
        service.approve_waiting(db)
    assert _statuses(Session)["about"] == "draft"


def test_waiting_drafts_of_old_games_are_approved(env, monkeypatch):
    _, _, Session, store, _ = env
    with Session() as db:
        service.generate_for(db, store, db.get(Game, "lagos-apocalypto"))  # drafts, as before the feature
        monkeypatch.setattr(settings, "game_pages_auto_approve", True)
        assert service.approve_waiting(db) >= 1
    assert "draft" not in _statuses(Session).values()


# ---- the daily agent's door: a secret key, quotes that must be real, nothing a person touched

AGENT = {"Authorization": "Bearer agent-secret"}
GOOD = {
    "title": "About Lagos Apocalypto", "description": "A zombie survival game set in Lagos.",
    "body": "## The story\nA zombie survival game set in Lagos. It begins at 8:11 a.m.",
    "quotes": ["A zombie survival game set in Lagos", "It begins at 8:11 a.m."],
}


def _on(monkeypatch):
    monkeypatch.setattr(settings, "agent_api_key", "agent-secret")


def test_the_agent_door_is_shut_without_the_key(env, monkeypatch):
    client, _, _, _, _ = env
    assert client.get("/agent/game-pages/todo").status_code == 404  # no key set at all
    _on(monkeypatch)
    assert client.get("/agent/game-pages/todo").status_code == 404
    assert client.get("/agent/game-pages/todo", headers={"Authorization": "Bearer wrong"}).status_code == 404
    assert client.get("/agent/game-pages/todo", headers=AGENT).status_code == 200


def test_todo_lists_games_with_their_facts_and_drops_finished_ones(env, monkeypatch):
    client, _, _, _, _ = env
    _on(monkeypatch)
    todo = client.get("/agent/game-pages/todo", headers=AGENT).json()
    assert {t["game"] for t in todo} == {"lagos-apocalypto", "bare"} and next(t for t in todo if t["game"] == "lagos-apocalypto")["facts"], todo
    assert client.put("/agent/game-pages/lagos-apocalypto/about", json=GOOD, headers=AGENT).status_code == 200
    assert {t["game"] for t in client.get("/agent/game-pages/todo", headers=AGENT).json()} == {"bare"}
    assert client.post("/agent/game-pages/bare/skip?reason=Nothing%20to%20say", headers=AGENT).status_code == 200
    assert client.get("/agent/game-pages/todo", headers=AGENT).json() == []


def test_a_saved_page_needs_real_quotes_and_passes_the_checks(env, monkeypatch):
    client, _, Session, _, _ = env
    _on(monkeypatch)
    monkeypatch.setattr(settings, "game_pages_auto_approve", True)
    url = "/agent/game-pages/lagos-apocalypto/about"
    assert client.put(url, json={**GOOD, "quotes": ["A zombie survival game set in Lagos"]}, headers=AGENT).status_code == 422  # too few
    assert client.put(url, json={**GOOD, "quotes": ["A zombie survival game set in Lagos", "it has dragons and a moat"]}, headers=AGENT).status_code == 422  # invented
    assert client.put(url, json={**GOOD, "body": "Visit https://spam.example now"}, headers=AGENT).status_code == 422
    res = client.put(url, json=GOOD, headers=AGENT)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "approved"
    with Session() as db:
        page = db.query(GamePage).filter_by(game_name="lagos-apocalypto", kind="about").one()
        assert page.writer == "agent" and page.approved_by_id is None


def test_the_agent_never_replaces_a_page_a_person_took_down(env, monkeypatch):
    client, tokens, Session, _, _ = env
    _on(monkeypatch)
    monkeypatch.setattr(settings, "game_pages_auto_approve", True)
    url = "/agent/game-pages/lagos-apocalypto/about"
    client.put(url, json=GOOD, headers=AGENT)
    with Session() as db:
        page = db.query(GamePage).filter_by(game_name="lagos-apocalypto", kind="about").one()
        service.unapprove(db, page.id)
    assert client.put(url, json=GOOD, headers=AGENT).status_code == 409
    assert "lagos-apocalypto" not in {t["game"] for t in client.get("/agent/game-pages/todo", headers=AGENT).json()}


# ---- the page layout: a hero, tabs, facts and cards, with the same words as before

def test_a_game_page_has_a_hero_tabs_facts_and_cards(env, monkeypatch):
    client, _, Session, store, _ = env
    monkeypatch.setattr(settings, "game_pages_auto_approve", True)
    with Session() as db:
        service.generate_for(db, store, db.get(Game, "lagos-apocalypto"))
    about = client.get("/pages/games/lagos-apocalypto/about").text
    assert 'class="gs-hero"' in about and 'class="gs-tabs"' in about and 'aria-current="page">About<' in about and 'class="gs-facts"' in about
    assert 'class="gs-card"' in about and "<kbd>W</kbd>" in about and "A zombie survival game set in Lagos" in about
    faq = client.get("/pages/games/lagos-apocalypto/faq").text
    assert '<details class="gs-qa"><summary>' in faq and "FAQPage" in faq  # questions open and close; the markup for search engines stays
