"""Live board window: a tiny local web page showing the current position as SVG.

The CLI pushes each position here; the page polls and redraws. Vector pieces
scale cleanly with the window. A fixed port lets an already-open window pick up
the next session without opening another one.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess
import chess.svg

COLORS = {
    "square light": "#e3c9a0",
    "square dark": "#a8784e",
    "square light lastmove": "#e6d77a",
    "square dark lastmove": "#c2ad52",
    "margin": "#2b2b2b",
    "coord": "#d8d8d8",
}

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>chesstrain</title>
<style>
  html, body { margin: 0; height: 100%; background: #1e1e1e; color: #ddd;
               font: 15px/1.4 "JetBrainsMono Nerd Font", monospace; }
  body { display: flex; flex-direction: column; }
  #caption { padding: 8px 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #board { flex: 1; min-height: 0; display: flex; justify-content: center; }
  #board svg { height: 100%; width: auto; max-width: 100%; }
  #log { padding: 6px 12px 10px; min-height: 4.2em; white-space: pre-wrap; color: #bbb; }
  .off { opacity: .35; }
</style></head>
<body>
  <div id="caption">waiting for chesstrain…</div>
  <div id="board"></div>
  <div id="log"></div>
<script>
let v = -1;
async function poll() {
  try {
    const r = await fetch('/state?v=' + v, {cache: 'no-store'});
    const s = await r.json();
    document.body.classList.remove('off');
    if (s.v !== v) {
      v = s.v;
      document.getElementById('board').innerHTML = s.svg;
      document.getElementById('caption').textContent = s.caption;
      document.getElementById('log').textContent = s.log.join('\\n');
    }
  } catch (e) {
    document.body.classList.add('off');
    document.getElementById('caption').textContent = 'chesstrain not running — this window reconnects next session';
  }
  setTimeout(poll, 250);
}
poll();
</script></body></html>
"""


class BoardWindow:
    def __init__(self, port: int = 8765):
        self.port = port
        self.version = 0
        self.svg = chess.svg.board(chess.Board(), colors=COLORS)
        self.caption = ""
        self.log: deque[str] = deque(maxlen=4)
        self.last_poll = 0.0
        self.lock = threading.Lock()
        self.server: ThreadingHTTPServer | None = None

    # -- state ----------------------------------------------------------------

    def show(self, board: chess.Board, orientation: chess.Color, caption: str = "") -> None:
        lastmove = board.peek() if board.move_stack else None
        check = board.king(board.turn) if board.is_check() else None
        svg = chess.svg.board(board, orientation=orientation, lastmove=lastmove, check=check, colors=COLORS)
        with self.lock:
            self.svg, self.caption = svg, caption
            self.log.clear()
            self.version += 1

    def say(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self.lock:
            self.log.append(text)
            self.version += 1

    def _state(self) -> bytes:
        with self.lock:
            self.last_poll = time.monotonic()
            return json.dumps(
                {"v": self.version, "svg": self.svg, "caption": self.caption, "log": list(self.log)}
            ).encode()

    # -- server ---------------------------------------------------------------

    def start(self, open_window: bool = True) -> str:
        window = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/state"):
                    body, ctype = window._state(), "application/json"
                elif self.path in ("/", "/index.html"):
                    body, ctype = PAGE.encode(), "text/html; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        except OSError:
            # Port busy (another session running?) — fall back to any free port.
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{self.port}/"
        if open_window:
            # Give a window left open from a previous session a moment to reconnect.
            time.sleep(1.0)
            if time.monotonic() - self.last_poll > 0.9:
                launch(url)
        return url

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()


def launch(url: str) -> None:
    if shutil.which("omarchy-launch-webapp"):
        subprocess.Popen(
            ["omarchy-launch-webapp", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
    else:
        webbrowser.open(url)
