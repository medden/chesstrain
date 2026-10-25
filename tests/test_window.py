import json
import urllib.request

import chess

from chesstrain.session import Display
from chesstrain.window import BoardWindow


def test_window_serves_page_and_state():
    w = BoardWindow(port=0)
    url = w.start(open_window=False)
    try:
        page = urllib.request.urlopen(url).read().decode()
        assert "chesstrain" in page and "/state" in page
        d = Display(colour=False, out=[], window=w)
        b = chess.Board()
        b.push_san("e4")
        d.board(b, chess.BLACK)
        d.say("✓ d6")
        state = json.loads(urllib.request.urlopen(url + "state").read())
        assert "<svg" in state["svg"] and state["caption"] == "1. e4"
        assert state["log"] == ["✓ d6"]
        assert d.out == ["✓ d6"]  # board went to the window, not the terminal
    finally:
        w.stop()


def _post(url, text, origin=None):
    req = urllib.request.Request(url + "input", data=json.dumps({"text": text}).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    if origin:
        req.add_header("Origin", origin)
    return urllib.request.urlopen(req)


def test_board_clicks_and_terminal_both_answer():
    import io

    from chesstrain.window import WindowMoveSource

    w = BoardWindow(port=0)
    url = w.start(open_window=False)
    try:
        b = chess.Board()
        # Window input is only accepted while a question is waiting.
        assert json.loads(_post(url, "e2e4").read())["accepted"] is False
        src = WindowMoveSource(w, stdin=io.StringIO(""))  # EOF -> "quit" queued from terminal
        assert src.next_input(b, "Your move: ") == "quit"
        w.inbox = __import__("queue").Queue()
        w.await_input(b, "Your move: ")
        state = json.loads(urllib.request.urlopen(url + "state").read())
        assert state["awaiting"] and "e2e4" in state["legal"]["e2"]
        assert json.loads(_post(url, "e2e4", origin=url.rstrip("/")).read())["accepted"] is True
        assert w.inbox.get_nowait() == ("window", "e2e4")
        # Another site posting to localhost is refused.
        w.await_input(b, "Your move: ")
        try:
            _post(url, "quit", origin="https://evil.example")
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as e:
            assert e.code == 403
    finally:
        w.stop()


def test_next_command_ends_drill_line():
    from test_core import make_rep

    from chesstrain.db import Progress
    from chesstrain.moves import ScriptedMoveSource
    from chesstrain.session import Trainer

    t = Trainer(make_rep(), Progress(":memory:"), ScriptedMoveSource(["next"]), Display(colour=False, out=[]))
    assert t.drill_line() is True
    assert t.tally.total == 0
