"""Live board window: a tiny local web page showing the current position as SVG.

The CLI pushes each position here; the page polls and redraws. Vector pieces
scale cleanly with the window. A fixed port lets an already-open window pick up
the next session without opening another one.

The page is also an input: click a piece then its destination, type (or dictate) into
the text box, or press the command buttons. WindowMoveSource merges those with
terminal input, so either answers the same question.
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sys
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

BUTTONS = [("hint", "Hint"), ("skip", "Skip"), ("next", "Next line"), ("help", "Help"), ("quit", "Quit")]

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>chesstrain</title>
<style>
  html, body { margin: 0; height: 100%; background: #1e1e1e; color: #ddd;
               font: 15px/1.4 "JetBrainsMono Nerd Font", monospace; }
  body { display: flex; flex-direction: column; }
  #caption { padding: 8px 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  #board { flex: 1; min-height: 0; display: flex; justify-content: center; padding: 6px; }
  #board svg { height: 100%; width: auto; max-width: 100%; border-radius: 4px;
               outline: 5px solid transparent; transition: outline-color .8s; }
  #board.good svg { outline-color: #5cb85c; transition: none; }
  #board.bad svg { outline-color: #d9534f; transition: none; }
  #board svg use { pointer-events: none; }
  .waiting #board rect.square { cursor: pointer; }
  #board rect.square.sel { fill: #7fa650; }
  #board rect.square.dest { fill: #a9c97f; }
  #bar { display: flex; gap: 8px; padding: 8px 12px 0; flex-wrap: wrap; }
  #entry { display: flex; gap: 8px; padding: 8px 12px 0; align-items: center; }
  #prompt { color: #e6d77a; white-space: nowrap; }
  #text { flex: 1; min-width: 0; font: inherit; background: #111; color: #eee; border: 1px solid #555;
          border-radius: 6px; padding: 4px 8px; }
  #text:disabled { opacity: .4; }
  button { font: inherit; background: #333; color: #ddd; border: 1px solid #555; border-radius: 6px;
           padding: 4px 12px; cursor: pointer; }
  button:hover:enabled { background: #444; }
  button:disabled { opacity: .4; cursor: default; }
  #log { padding: 6px 12px 10px; min-height: 4.2em; max-height: 38vh; overflow: auto;
         white-space: pre-wrap; color: #bbb; }
  #log .ok { color: #7fd17f; } #log .bad { color: #ef7d7a; }
  .off { opacity: .35; }
</style></head>
<body>
  <div id="caption">waiting for chesstrain…</div>
  <div id="board"></div>
  <div id="bar">__BUTTONS__</div>
  <div id="entry"><span id="prompt"></span>
    <input id="text" autocomplete="off" spellcheck="false" placeholder="type or say a move" disabled></div>
  <div id="log"></div>
<script>
let v = -1, state = null, selected = null, seenVerdict = null, flashTimer = null;
const text = document.getElementById('text');

function send(text) {
  if (!state || !state.awaiting) return;
  state.awaiting = false;
  document.body.classList.remove('waiting');
  text.disabled = true;
  fetch('/input', {method: 'POST', headers: {'Content-Type': 'application/json'},
                   body: JSON.stringify({text})});
}

function mark() {
  document.querySelectorAll('#board rect.square').forEach(r => {
    const sq = [...r.classList].find(c => /^[a-h][1-8]$/.test(c));
    r.classList.toggle('sel', sq === selected);
    const dests = selected && state.legal[selected] ? state.legal[selected].map(u => u.slice(2, 4)) : [];
    r.classList.toggle('dest', dests.includes(sq));
  });
}

document.getElementById('board').addEventListener('click', e => {
  const r = e.target.closest('rect.square');
  if (!r || !state || !state.awaiting) return;
  const sq = [...r.classList].find(c => /^[a-h][1-8]$/.test(c));
  if (selected && state.legal[selected]) {
    // Promotions arrive as e7e8q/e7e8r/...; the queen is listed first.
    const move = state.legal[selected].find(u => u.slice(2, 4) === sq);
    if (move) { selected = null; mark(); send(move); return; }
  }
  selected = state.legal[sq] ? sq : null;
  mark();
  text.focus();
});

text.addEventListener('keydown', e => {
  if (e.key === 'Enter' && text.value.trim()) { send(text.value.trim()); text.value = ''; }
});

function flash(ok) {
  const b = document.getElementById('board');
  clearTimeout(flashTimer);
  b.classList.remove('good', 'bad');
  b.classList.add(ok ? 'good' : 'bad');
  flashTimer = setTimeout(() => b.classList.remove('good', 'bad'), 1500);
}

document.querySelectorAll('#bar button').forEach(b =>
  b.addEventListener('click', () => send(b.dataset.cmd)));

async function poll() {
  try {
    const r = await fetch('/state?v=' + v, {cache: 'no-store'});
    const s = await r.json();
    document.body.classList.remove('off');
    if (s.v !== v) {
      v = s.v; state = s;
      document.getElementById('board').innerHTML = s.svg;
      document.getElementById('caption').textContent = s.caption;
      document.getElementById('prompt').textContent = s.awaiting ? s.prompt : '';
      const log = document.getElementById('log');
      log.replaceChildren(...s.log.map(line => {
        const d = document.createElement('div');
        d.textContent = line;
        if (line.startsWith('✓')) d.className = 'ok';
        if (line.startsWith('✗')) d.className = 'bad';
        return d;
      }));
      log.scrollTop = log.scrollHeight;
      // Flash only for answers given while this page was open, not on (re)load.
      if (seenVerdict !== null && s.verdict.id !== seenVerdict) flash(s.verdict.ok);
      seenVerdict = s.verdict.id;
      text.disabled = !s.awaiting;
      if (s.awaiting) text.focus();
      document.body.classList.toggle('waiting', s.awaiting);
      document.querySelectorAll('#bar button').forEach(b => b.disabled = !s.awaiting);
      if (!s.awaiting || (selected && !s.legal[selected])) selected = null;
      mark();
    }
  } catch (e) {
    document.body.classList.add('off');
    document.getElementById('caption').textContent = 'chesstrain not running — this window reconnects next session';
  }
  setTimeout(poll, 250);
}
poll();
</script></body></html>
""".replace("__BUTTONS__", "".join(f'<button data-cmd="{c}" disabled>{label}</button>' for c, label in BUTTONS))


def _legal(board: chess.Board) -> dict[str, list[str]]:
    """from-square -> UCI moves, queen promotion first."""
    out: dict[str, list[str]] = {}
    order = {chess.QUEEN: 0, chess.KNIGHT: 1, chess.ROOK: 2, chess.BISHOP: 3, None: 0}
    for move in sorted(board.legal_moves, key=lambda m: order[m.promotion]):
        out.setdefault(chess.square_name(move.from_square), []).append(move.uci())
    return out


class BoardWindow:
    def __init__(self, port: int = 8765):
        self.port = port
        self.version = 0
        self.svg = chess.svg.board(chess.Board(), colors=COLORS)
        self.caption = ""
        self.log: deque[str] = deque(maxlen=6)
        self.awaiting = False
        self.prompt = ""
        self.legal: dict[str, list[str]] = {}
        self.verdict_id, self.verdict_ok = 0, False
        self.inbox: queue.Queue[tuple[str, str]] = queue.Queue()
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

    def verdict(self, ok: bool) -> None:
        with self.lock:
            self.verdict_id, self.verdict_ok = self.verdict_id + 1, ok
            self.version += 1

    def await_input(self, board: chess.Board, prompt: str) -> None:
        with self.lock:
            self.awaiting, self.prompt, self.legal = True, prompt.strip(), _legal(board)
            self.version += 1

    def input_done(self) -> None:
        with self.lock:
            self.awaiting, self.legal = False, {}
            self.version += 1

    def _state(self) -> bytes:
        with self.lock:
            self.last_poll = time.monotonic()
            return json.dumps(
                {
                    "v": self.version,
                    "svg": self.svg,
                    "caption": self.caption,
                    "log": list(self.log),
                    "awaiting": self.awaiting,
                    "prompt": self.prompt,
                    "legal": self.legal,
                    "verdict": {"id": self.verdict_id, "ok": self.verdict_ok},
                }
            ).encode()

    def _receive(self, text: str) -> bool:
        with self.lock:
            if not self.awaiting:
                return False
            self.awaiting = False
            self.version += 1
        self.inbox.put(("window", text))
        return True

    # -- server ---------------------------------------------------------------

    def start(self, open_window: bool = True) -> str:
        window = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, body: bytes, ctype: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/state"):
                    self._send(window._state(), "application/json")
                elif self.path in ("/", "/index.html"):
                    self._send(PAGE.encode(), "text/html; charset=utf-8")
                else:
                    self.send_error(404)

            def do_POST(self):
                # Only our own page may answer (blocks other sites posting to localhost).
                origin = self.headers.get("Origin")
                host = self.headers.get("Host", "")
                if self.path != "/input" or (origin and origin != f"http://{host}"):
                    self.send_error(403)
                    return
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    text = str(json.loads(self.rfile.read(length)).get("text", ""))[:40]
                except (ValueError, AttributeError):
                    self.send_error(400)
                    return
                accepted = window._receive(text)
                self._send(json.dumps({"accepted": accepted}).encode(), "application/json")

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


class WindowMoveSource:
    """MoveSource that takes whichever arrives first: a terminal line or a window click/button."""

    def __init__(self, window: BoardWindow, stdin=None):
        self.window = window
        stdin = stdin if stdin is not None else sys.stdin
        threading.Thread(target=self._read, args=(stdin,), daemon=True).start()

    def _read(self, stdin) -> None:
        for line in stdin:
            self.window.inbox.put(("terminal", line.strip()))
        self.window.inbox.put(("terminal", "quit"))  # EOF

    def next_input(self, board: chess.Board, prompt: str) -> str:
        self.window.await_input(board, prompt)
        print(prompt, end="", flush=True)
        try:
            source, text = self.window.inbox.get()
        except KeyboardInterrupt:
            print()
            source, text = "terminal", "quit"
        self.window.input_done()
        if source == "window":
            try:
                shown = board.san(chess.Move.from_uci(text))
            except (ValueError, AssertionError):
                shown = text  # a button command
            print(f"{shown}   (from board)")
        return text


def launch(url: str) -> None:
    if shutil.which("omarchy-launch-webapp"):
        subprocess.Popen(
            ["omarchy-launch-webapp", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
    else:
        webbrowser.open(url)
