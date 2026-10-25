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
