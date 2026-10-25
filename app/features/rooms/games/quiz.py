"""Quiz, a Kahoot-style game for a room of up to 100 people. The person who makes the room is the host (seat 0): they set the
questions up, wait for people to join with the code, and start it. Everyone else answers on their own screen.

Setup (sent by the host before the start): {"title": "...", "questions": [{"q": "...", "answers": ["...", "..."], "correct": 0, "seconds": 20}]}
(2 to 4 answers; 1 to 50 questions; seconds 5 to 120, 20 if left out).

Moves: a player sends {"answer": 0-3} once per question; the host sends {"skip": true} to close the question that is open, or to move
on from the answers.

Scoring is done here, by the server's clock: a right answer is worth 500, plus up to 500 more for how much of the time was left
(counted in half seconds). A wrong or missing answer is worth 0. The right answer is never sent to anyone before the question closes.

State is per phase: "ready" (a short countdown), "question", "reveal" (the right answer and the scores) and "over".
"""
import time

name = "quiz"
start_mode = "host"
min_players = 2  # the host and one player
max_players = 101  # the host and 100 players
forfeit = False  # someone leaving must not end it for the rest

MAX_QUESTIONS = 50
READY_MS = 3000
REVEAL_MS = 6000
BASE_POINTS = 500
SPEED_POINTS = 500
BUCKET_MS = 500
LIMITS = {"title": 80, "q": 200, "answer": 100}


def now_ms() -> int:
    return int(time.time() * 1000)  # tests replace this


def _text(value, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def validate_setup(data) -> tuple[dict | None, str | None]:
    if not isinstance(data, dict):
        return None, "Send the quiz as a title and a list of questions."
    raw = data.get("questions")
    if not isinstance(raw, list) or not raw:
        return None, "Add at least one question."
    if len(raw) > MAX_QUESTIONS:
        return None, f"A quiz can have up to {MAX_QUESTIONS} questions."
    questions = []
    for n, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            return None, f"Question {n} is not a question."
        q = _text(item.get("q"), LIMITS["q"])
        answers = item.get("answers")
        if not q:
            return None, f"Question {n} needs some text."
        if not isinstance(answers, list) or not 2 <= len(answers) <= 4:
            return None, f"Question {n} needs 2 to 4 answers."
        answers = [_text(a, LIMITS["answer"]) for a in answers]
        if not all(answers):
            return None, f"Question {n} has an empty answer."
        correct = item.get("correct")
        if not isinstance(correct, int) or isinstance(correct, bool) or not 0 <= correct < len(answers):
            return None, f"Question {n}: pick which answer is right."
        seconds = item.get("seconds", 20)
        if not isinstance(seconds, int) or isinstance(seconds, bool) or not 5 <= seconds <= 120:
            return None, f"Question {n}: the time must be 5 to 120 seconds."
        questions.append({"q": q, "answers": answers, "correct": correct, "seconds": seconds})
    return {"title": _text(data.get("title"), LIMITS["title"]) or "Quiz", "questions": questions}, None


def _open(state: dict, index: int, now: int) -> None:
    question = state["questions"][index]
    state.update(phase="ready" if index == 0 else "question", index=index, answers={}, last_points={}, reveal_until_ms=None)
    state["started_ms"] = now + (READY_MS if index == 0 else 0)
    state["deadline_ms"] = state["started_ms"] + question["seconds"] * 1000


def new_state(players: int, options: dict | None = None) -> dict:
    if not options:
        raise ValueError("Set the questions up first.")
    state = {"title": options["title"], "questions": options["questions"], "scores": {}, "streaks": {}, "players": set()}
    for seat in range(1, players):
        state["players"].add(seat)
        state["scores"][seat] = 0
    _open(state, 0, now_ms())
    return state


def add_player(state: dict, seat: int) -> dict:
    if seat not in state["players"]:
        state["players"].add(seat)
        state["scores"][seat] = 0
    return state


def _phase_now(state: dict, now: int) -> str:
    """'ready' until the first question's start, then 'question' while there is time."""
    if state["phase"] == "ready" and now >= state["started_ms"]:
        state["phase"] = "question"
    return state["phase"]


def _close(state: dict, now: int) -> None:
    """The question is over: score it and show the answers."""
    question = state["questions"][state["index"]]
    total = question["seconds"] * 1000
    state["counts"] = [0] * len(question["answers"])
    state["last_points"] = {}
    for seat, (choice, at) in state["answers"].items():
        state["counts"][choice] += 1
        if choice == question["correct"]:
            left = max(0, state["deadline_ms"] - at)
            bucket = (left // BUCKET_MS) * BUCKET_MS
            points = BASE_POINTS + round(SPEED_POINTS * bucket / total)
            state["scores"][seat] = state["scores"].get(seat, 0) + points
            state["last_points"][seat] = points
    state["phase"] = "reveal"
    state["reveal_until_ms"] = now + REVEAL_MS


def _next(state: dict, now: int) -> None:
    if state["index"] + 1 >= len(state["questions"]):
        state["phase"] = "over"
        return
    _open(state, state["index"] + 1, now)


def tick(state: dict, now: int) -> tuple[dict, bool]:
    """Called every second by the rooms core: closes a question whose time is up (or that everyone answered) and moves on."""
    phase = state["phase"]
    if phase == "ready":
        if now >= state["started_ms"]:
            state["phase"] = "question"
            return state, True
        return state, False
    if phase == "question":
        everyone = state["players"] and all(seat in state["answers"] for seat in state["players"])
        if now >= state["deadline_ms"] or everyone:
            _close(state, now)
            return state, True
    elif phase == "reveal" and now >= state["reveal_until_ms"]:
        _next(state, now)
        return state, True
    return state, False


def turn(state: dict) -> int | None:
    return None  # nobody takes turns


def apply(state: dict, seat: int, move) -> tuple[dict, str | None]:
    if state["phase"] == "over":
        return state, "The quiz is over."
    if not isinstance(move, dict):
        return state, "Send an answer."
    now = now_ms()
    if seat == 0:
        if not move.get("skip"):
            return state, "The host moves things on with skip."
        if _phase_now(state, now) == "question":
            _close(state, now)
        elif state["phase"] == "reveal":
            _next(state, now)
        return state, None
    if seat not in state["players"]:
        return state, "You are not playing."
    choice = move.get("answer")
    question = state["questions"][state["index"]]
    if not isinstance(choice, int) or isinstance(choice, bool) or not 0 <= choice < len(question["answers"]):
        return state, f"Pick an answer from 0 to {len(question['answers']) - 1}."
    if _phase_now(state, now) != "question" or now >= state["deadline_ms"]:
        return state, "That question is closed."
    if seat in state["answers"]:
        return state, "You already answered."
    state["answers"][seat] = (choice, now)
    return state, None


def _standings(state: dict) -> list[dict]:
    ranked = sorted(state["scores"].items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"seat": seat, "score": score, "rank": rank} for rank, (seat, score) in enumerate(ranked, 1)]


def result(state: dict) -> dict | None:
    if state["phase"] != "over":
        return None
    standings = _standings(state)
    return {"winner": standings[0]["seat"] if standings else None, "reason": "finished", "standings": standings}


def view(state: dict, seat: int) -> dict:
    phase = state["phase"]
    question = state["questions"][state["index"]]
    out = {
        "title": state["title"], "phase": phase, "index": state["index"], "total": len(state["questions"]),
        "players": len(state["players"]), "answered": len(state["answers"]),
        "host": seat == 0,
    }
    if phase in ("ready", "question"):
        out.update(q=question["q"], answers=question["answers"], seconds=question["seconds"],
                   started_ms=state["started_ms"], deadline_ms=state["deadline_ms"])
        if seat in state["answers"]:
            out["choice"] = state["answers"][seat][0]
    elif phase == "reveal":
        out.update(q=question["q"], answers=question["answers"], correct=question["correct"], counts=state["counts"],
                   reveal_until_ms=state["reveal_until_ms"], top=_standings(state)[:5])
        if seat in state["answers"]:
            out["choice"] = state["answers"][seat][0]
    if phase in ("reveal", "over") and seat != 0:
        out["score"] = state["scores"].get(seat, 0)
        out["points"] = state["last_points"].get(seat, 0) if phase == "reveal" else None
        rank = next((s["rank"] for s in _standings(state) if s["seat"] == seat), None)
        out["rank"] = rank
    if phase == "over":
        out["standings"] = _standings(state)
    return out


def bot_move(state: dict, seat: int, level: str):
    return None  # no bots in a quiz
