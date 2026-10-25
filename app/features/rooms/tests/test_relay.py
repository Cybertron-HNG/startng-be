import pytest
from starlette.websockets import WebSocketDisconnect

from app.features.rooms import core
from app.live import create_ticket
from app.main import app
from app.models import Game
from app.routers.live import get_sessionmaker

from .test_rooms import env  # noqa: F401

GAME = "tap-race"  # a published game that start.ng does not referee


def url(user_id, room="new", seats=None, game=GAME):
    extra = f"&seats={seats}" if seats else ""
    return f"/rooms/live?game={game}&ticket={create_ticket(user_id, game)}&room={room}{extra}"


@pytest.fixture
def live(env):  # noqa: F811
    client, ids, now = env
    with app.dependency_overrides[get_sessionmaker]()() as db:
        db.add(Game(name=GAME, owner_id=ids["ada"], repo_url="https://github.com/ada/tap-race"))
        db.commit()
    return client, ids, now


def test_a_relay_room_is_open_from_the_start_and_says_how_big_it_is(live):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"], seats=3)) as ada:
        welcome = ada.receive_json()
        assert welcome["status"] == "playing" and welcome["max"] == 3 and welcome["host"] == 0
        assert welcome["shared"] == {"version": 0, "data": None} and welcome["players"][0]["host"] is True
        code = welcome["room"]
        with client.websocket_connect(url(ids["tunde"], code)) as tunde:
            assert tunde.receive_json()["you"] == 1
            assert ada.receive_json()["type"] == "join"
            with client.websocket_connect(url(ids["chi"], code)) as chi:
                assert chi.receive_json()["you"] == 2
        assert core.rooms[code].rules.max_players == 3


def test_the_room_size_is_kept_between_2_and_8(live):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"], seats=100)) as ada:
        assert ada.receive_json()["max"] == 8
    with client.websocket_connect(url(ids["tunde"], seats=1)) as tunde:
        assert tunde.receive_json()["max"] == 2


def test_a_full_relay_room_refuses_more_people(live):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"], seats=2)) as ada:
        code = ada.receive_json()["room"]
        with client.websocket_connect(url(ids["tunde"], code)) as tunde:
            tunde.receive_json()
            with pytest.raises(WebSocketDisconnect) as full:
                with client.websocket_connect(url(ids["chi"], code)) as chi:
                    chi.receive_json()
            assert full.value.code == core.ROOM_FULL


def test_messages_go_to_everyone_else_or_to_one_seat_and_say_who_sent_them(live):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"], seats=3)) as ada:
        code = ada.receive_json()["room"]
        with client.websocket_connect(url(ids["tunde"], code)) as tunde:
            tunde.receive_json()
            ada.receive_json()
            with client.websocket_connect(url(ids["chi"], code)) as chi:
                chi.receive_json()
                ada.receive_json(), tunde.receive_json()
                ada.send_json({"type": "send", "data": {"x": 1, "shot": True}})
                assert tunde.receive_json() == {"type": "message", "from": 0, "data": {"x": 1, "shot": True}}
                assert chi.receive_json()["from"] == 0
                tunde.send_json({"type": "send", "to": 2, "data": "psst"})
                assert chi.receive_json() == {"type": "message", "from": 1, "data": "psst"}
                tunde.send_json({"type": "send", "to": 5, "data": "nobody"})
                tunde.send_json({"type": "ping"})
                assert tunde.receive_json()["type"] == "pong"  # the sender got nothing back, and seat 5 does not exist
                ada.send_json({"type": "ping"})
                assert ada.receive_json()["type"] == "pong"  # the sender does not hear itself


def test_messages_and_moves_are_limited(live):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"])) as ada:
        ada.receive_json()
        ada.send_json({"type": "send", "data": "x" * (core.MAX_MESSAGE + 1)})
        assert ada.receive_json()["code"] == "too_big"
        ada.send_json({"type": "move", "data": {"cell": 1}})  # a Relay room has no referee
        ada.send_json({"type": "resign"})
        ada.send_json({"type": "bot"})
        ada.send_json({"type": "start"})
        ada.send_json({"type": "ping"})
        assert ada.receive_json()["type"] == "pong"
        assert len(core.rooms[next(iter(core.rooms))].seats) == 1  # no bot was added


def test_shared_state_is_versioned_and_late_joiners_get_it(live):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"], seats=3)) as ada:
        code = ada.receive_json()["room"]
        with client.websocket_connect(url(ids["tunde"], code)) as tunde:
            tunde.receive_json()
            ada.receive_json()
            ada.send_json({"type": "set_state", "version": 0, "data": {"board": [0] * 9, "turn": 0}})
            for who in (ada, tunde):
                assert who.receive_json() == {"type": "room_state", "version": 1, "data": {"board": [0] * 9, "turn": 0}, "by": 0}
            tunde.send_json({"type": "set_state", "version": 1, "data": {"turn": 1}})
            assert tunde.receive_json()["version"] == 2 and ada.receive_json()["version"] == 2
            ada.send_json({"type": "set_state", "version": 1, "data": {"turn": 5}})  # written from a version that is already old
            error = ada.receive_json()
            assert error["code"] == "state_conflict" and error["version"] == 2 and error["data"] == {"turn": 1}
    with client.websocket_connect(url(ids["chi"], code)) as chi:
        late = chi.receive_json()
        assert late["shared"] == {"version": 2, "data": {"turn": 1}}


def test_a_conflicting_write_gets_the_newer_state_back(live):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"])) as ada:
        ada.receive_json()
        ada.send_json({"type": "set_state", "version": 0, "data": {"n": 1}})
        ada.receive_json()
        ada.send_json({"type": "set_state", "version": 0, "data": {"n": 2}})
        error = ada.receive_json()
        assert error["code"] == "state_conflict" and error["version"] == 1 and error["data"] == {"n": 1}


def test_shared_state_is_limited_to_8_kb_but_may_be_bigger_than_a_message(live):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"])) as ada:
        ada.receive_json()
        ada.send_json({"type": "set_state", "version": 0, "data": {"text": "x" * 4000}})  # over 2 KB, under 8 KB
        assert ada.receive_json()["type"] == "room_state"
        ada.send_json({"type": "set_state", "version": 1, "data": {"text": "x" * 9000}})
        assert ada.receive_json()["code"] == "too_big"


def test_a_dropped_relay_player_comes_back_to_the_same_seat(live):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"], seats=3)) as ada:
        code = ada.receive_json()["room"]
        with client.websocket_connect(url(ids["tunde"], code)) as tunde:
            tunde.receive_json()
            ada.receive_json()
            tunde.send_json({"type": "ping"})
            tunde.receive_json()
        assert ada.receive_json()["type"] == "away"
        with client.websocket_connect(url(ids["tunde"], code)) as again:
            assert again.receive_json()["you"] == 1


def test_referee_games_and_matchmaking_still_behave(live):
    client, ids, _ = live
    with client.websocket_connect(url(ids["ada"], "match")) as ada:
        assert ada.receive_json()["code"] == "no_match"  # Relay rooms are for friends with a code
    with client.websocket_connect(url(ids["tunde"], seats=3, game="tic-tac-toe")) as ttt:
        assert ttt.receive_json()["status"] == "waiting"  # a refereed game ignores seats
