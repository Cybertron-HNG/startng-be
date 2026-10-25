"""The brief read from a game's repo source (app/brief.py): the game's own words, never a library's or the platform's."""
import io
import zipfile

from app import brief
from tests.test_publish import env  # noqa: F401 (fixture)

GAME_JS = '''// Relic Hunter: a temple escape with a rolling boulder behind you.
// Copyright (c) 2026 Someone. Licensed under MIT.
const hint1 = "RUN! Escape the boulder - get back through the vault door";
const hint2 = 'The vault is open. Take the golden idol';
const win = `You escaped with the idol!`;
const fragment = "s explorer gear, worn over the player and more";
const lib = "THREE.Vector2: index is out of range: with extra words here";
const platform = "Sign in with start.ng to save your run and more words";
const code = "const a = b + c; return value from this function now";
const url = "https://example.com/some/long/path/to/an/image.png file";
const tiny = "Too short";
const interpolated = `Score is ${score} and you are doing really well today`;
'''


def make(files: dict[str, str | bytes]):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    zf = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    return zf, {i.filename: i for i in zf.infolist()}


def test_it_keeps_the_games_own_words_and_nothing_else():
    zf, entries = make({
        "src/tomb/game.js": GAME_JS,
        "src/tomb/boulder.js": "export const x = 1;",
        "index.html": "<html><body><p>Escape the temple before the lights go out forever.</p><script>var s = 'Not this one, it is in a script tag';</script></body></html>",
        "src/lib/three.js": 'const a = "Hello from a library, this should never be read at all";',
        "dist/index.html": "<p>Built output that must not be read as the games words.</p>",
        "node_modules/x/index.js": 'const a = "Hello from a package, this should never be read at all";',
        "src/bundle.js": 'const a = "Hello from a bundle, this should never be read at all"; ' + "x" * 2000,
        "package.json": '{"description": "A package description that is long enough to count"}',
    })
    b = brief.extract(entries, zf, readme=b"# Relic Hunter\n\nA temple escape.")
    assert set(b["messages"]) == {
        "RUN! Escape the boulder - get back through the vault door",
        "The vault is open. Take the golden idol",
        "You escaped with the idol!",
        "Escape the temple before the lights go out forever.",  # from the page's own text, not its script
    }
    assert b["notes"] == ["Relic Hunter: a temple escape with a rolling boulder behind you."]  # not the licence line
    assert b["readme"].startswith("# Relic Hunter")
    assert "tomb" in b["names"] and "boulder" in b["names"] and "src" not in b["names"] and "game" not in b["names"]
    assert b["files"] == 3  # game.js, boulder.js, index.html: not the library, the build, the package or the bundle
    assert b["v"] == 1 and b["at"]


def test_nothing_from_libraries_the_platform_or_code_gets_in():
    zf, entries = make({"src/game.js": GAME_JS})
    text = " | ".join(brief.extract(entries, zf)["messages"])
    for bad in ("THREE", "Sign in", "start.ng", "const a", "https://", "Too short", "Score is", "explorer gear"):
        assert bad not in text, bad


def test_it_is_capped_and_never_reads_a_huge_file():
    lines = "\n".join(f'const m{i} = "Message number {i} is a sentence with enough words in it";' for i in range(100))
    zf, entries = make({"src/many.js": lines, "src/huge.js": "x\n" * 200_000})
    b = brief.extract(entries, zf)
    assert len(b["messages"]) == brief.MAX_MESSAGES
    assert b["files"] == 1  # the huge file is skipped without being read


def test_readme_is_cut_and_a_missing_one_is_none():
    zf, entries = make({"src/a.js": "const a = 1;"})
    assert brief.extract(entries, zf)["readme"] is None
    assert len(brief.extract(entries, zf, readme=b"word " * 10_000)["readme"]) <= brief.MAX_README


def test_the_readme_next_to_the_game_wins_over_the_one_at_the_top():
    zf, entries = make({"README.md": "top", "game/README.md": "in the game folder"})
    assert brief.find_readme(entries, zf, "game/") == b"in the game folder"
    assert brief.find_readme(entries, zf, "") == b"top"
    zf2, entries2 = make({"src/a.js": "x"})
    assert brief.find_readme(entries2, zf2, "") is None


# ---- kept on the game when it is published

def test_publishing_keeps_the_brief_on_the_game(env):
    from app.db import get_db
    from app.main import app
    from app.models import Game
    from tests.test_publish import GAME, make_zip

    client, _, tokens, archives = env
    archives["zip"] = make_zip({**GAME, "README.md": "# Lane Dash\n\nDodge the lava and climb as high as you can.", "src/story.js": GAME_JS})
    res = client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash"}, headers=tokens["ada"])
    assert res.status_code == 200, res.text
    db = next(app.dependency_overrides[get_db]())
    kept = db.get(Game, "lane-dash").brief
    assert kept["readme"].startswith("# Lane Dash")
    assert "The vault is open. Take the golden idol" in kept["messages"]
    assert not any("THREE" in m or "start.ng" in m for m in kept["messages"])

    # publishing again with different words replaces it
    archives["zip"] = make_zip({**GAME, "src/story.js": 'const a = "A completely different line about a new level here";'})
    client.post("/games/publish", json={"repo_url": "https://github.com/ada/Lane-Dash"}, headers=tokens["ada"])
    db.expire_all()
    assert db.get(Game, "lane-dash").brief["messages"] == ["A completely different line about a new level here"]
