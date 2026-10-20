"""Game standards (app/checks.py, standards()): a link back to start.ng, the start.ng name, and a share-on-X link.
A game that misses any is refused when it's published or updated."""
import pytest

from app.checks import standards
from app.config import settings
from app.publish import GameRejected, read_game
from tests.test_publish import GAME, env, make_zip  # noqa: F401  (env is a fixture)

# The smallest game that meets every standard.
COMPLIANT = {
    **GAME,
    "index.html": (
        '<!doctype html><script type="module" src="src/game.js"></script>'
        '<footer>Play more on <a href="https://start.ng">start.ng</a></footer>'
    ),
    "src/game.js": "share.onclick = () => open('https://twitter.com/intent/tweet?text=' + encodeURIComponent('I won!'));",
}


@pytest.fixture(autouse=True)
def enforced(monkeypatch):
    monkeypatch.setattr(settings, "enforce_game_standards", True)


def missing(files):
    return [p.code for p in standards([(path, data.encode() if isinstance(data, str) else data) for path, data in files.items()])]


def test_a_game_that_meets_the_standards_has_nothing_missing():
    assert missing(COMPLIANT) == []


def test_a_game_with_none_of_them_is_missing_all_three():
    assert missing(GAME) == ["missing_back_link", "missing_branding", "missing_share"]


@pytest.mark.parametrize(
    "page",
    [
        '<a href="https://start.ng">Back</a> start.ng',
        "<a href='https://start.ng/'>start.ng</a>",
        '<a href="https://www.start.ng/?from=game">start.ng</a>',
        "<script>location.href = `https://start.ng`; // start.ng</script>",
    ],
)
def test_different_ways_of_linking_back_count(page):
    files = {**COMPLIANT, "index.html": page + '<script src="src/game.js"></script>'}
    assert missing(files) == []


@pytest.mark.parametrize(
    "link",
    [
        "https://start.ng/connect",  # a page games use, not a way back
        "https://start.ng/games",
        "https://api.start.ng",
        "https://start.ng.evil.com",
        "https://notstart.ng",
    ],
)
def test_other_addresses_dont_count_as_a_link_back(link):
    files = {**COMPLIANT, "index.html": f'<a href="{link}">go</a><script src="src/game.js"></script>'}
    assert "missing_back_link" in missing(files)


def test_the_name_has_to_be_written_not_only_part_of_an_address():
    only_a_link = {**COMPLIANT, "index.html": '<a href="https://start.ng"><img src="logo.png"></a><script src="src/game.js"></script>'}
    assert missing(only_a_link) == ["missing_branding"]


@pytest.mark.parametrize("url", ["https://twitter.com/intent/tweet?text=hi", "https://x.com/intent/post?text=hi", "https://x.com/intent/tweet"])
def test_either_twitter_or_x_share_links_count(url):
    assert missing({**COMPLIANT, "src/game.js": f"open('{url}')"}) == []


def test_a_share_link_in_a_picture_or_text_file_doesnt_count():
    assert "missing_share" in missing({**COMPLIANT, "src/game.js": "// nothing", "notes.txt": "https://twitter.com/intent/tweet"})


def test_publishing_refuses_a_game_that_misses_them_and_says_how_to_fix_it():
    with pytest.raises(GameRejected) as err:
        read_game(make_zip(GAME))
    assert [e.code for e in err.value.errors] == ["missing_back_link", "missing_branding", "missing_share"]
    assert "https://start.ng/docs#game-standards" in err.value.errors[0].message


def test_publishing_accepts_a_game_that_meets_them():
    assert read_game(make_zip(COMPLIANT)).files


def test_the_standards_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(settings, "enforce_game_standards", False)
    assert read_game(make_zip(GAME)).files


def test_the_api_reports_them_too(env):
    client, _, tokens, archives = env
    archives["zip"] = make_zip(GAME)
    res = client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash"}, headers=tokens["ada"])
    assert res.status_code == 422
    assert {e["code"] for e in res.json()["errors"]} >= {"missing_back_link", "missing_branding", "missing_share"}
    archives["zip"] = make_zip(COMPLIANT)
    assert client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash"}, headers=tokens["ada"]).status_code == 200


def test_a_game_that_stops_meeting_them_keeps_its_live_version(env):
    client, storage, tokens, archives = env
    repo = {"repo_url": "https://github.com/ada/Lane-Dash"}
    archives["zip"] = make_zip(COMPLIANT)
    assert client.post("/games/publish", json=repo, headers=tokens["ada"]).status_code == 200
    archives["zip"] = make_zip({**COMPLIANT, "src/game.js": "console.log('no share button any more')"})
    res = client.post("/games/publish", json=repo, headers=tokens["ada"])
    assert res.status_code == 422 and [e["code"] for e in res.json()["errors"]] == ["missing_share"]
    assert "intent/tweet" in (storage.root / "lane-dash" / "src/game.js").read_text()  # the old version is still up
    assert [g["name"] for g in client.get("/games").json()] == ["lane-dash"]
