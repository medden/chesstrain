"""Move input: parsing typed moves and the MoveSource seam.

A MoveSource yields raw user input for a position. The CLI reads from stdin;
an electronic board could implement the same protocol later and return the
move it detects (as UCI) instead.
"""

from __future__ import annotations

import re
from typing import Protocol

import chess

from .spoken import spoken_to_move

COMMANDS = {
    "hint": "hint",
    "skip": "skip",
    "quit": "quit",
    "q": "quit",
    "exit": "quit",
    "stop": "quit",
    "board": "board",
    "next": "next",
    "nextline": "next",
    "help": "help",
    "?": "help",
}


def command_of(text: str) -> str | None:
    """'Hint.' / 'QUIT' / 'skip!' -> canonical command (dictation adds punctuation)."""
    word = re.sub(r"[^a-z?]", "", text.lower())
    return COMMANDS.get(word)


def notation_help(colour: chess.Color) -> str:
    """Cheat sheet for move notation; castling sides described from the player's seat."""
    short_side, long_side = ("right", "left") if colour == chess.WHITE else ("left", "right")
    return f"""\
Move notation (type it, or say it):
  Pieces    K king · Q queen · R rook · B bishop · N knight (N, because K is the king)
  Pawns     just the square: e4  ("e4", "pawn to e4")
  Captures  x = takes: Nxe5 ("knight takes e5"), exd5 ("e takes d5": pawn on the e-file)
  Castling  castle {short_side}: O-O    (short / kingside)   — or type/say "castle {short_side}"
            castle {long_side}: O-O-O  (long / queenside)   — or type/say "castle {long_side}"
            (0-0, oo and ooo also work)
  Two pieces can reach the square? Add the file or rank they start on:
            Nbd7 ("knight b to d7"), R1e2
  Promotion e8=Q or e8Q ("e8 queen")
  Check +, mate #  (optional; you can leave them off)
  Also fine: from-to squares like g1f3, and any upper/lower case.
  Board window: click a piece, then its square.
Commands: hint (shows which piece, counts as 'hard') · skip · next (next line) · board · quit · ? (this help)"""


class MoveSource(Protocol):
    def next_input(self, board: chess.Board, prompt: str) -> str: ...


class CliMoveSource:
    def next_input(self, board: chess.Board, prompt: str) -> str:
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return "quit"


class ScriptedMoveSource:
    """Feeds canned inputs; used by tests."""

    def __init__(self, inputs: list[str]):
        self.inputs = list(inputs)

    def next_input(self, board: chess.Board, prompt: str) -> str:
        return self.inputs.pop(0) if self.inputs else "quit"


def parse_move(board: chess.Board, text: str) -> chess.Move | None:
    """Parse SAN (case-tolerant), UCI, or spoken words. None if illegal/unreadable."""
    raw = text
    text = text.strip().rstrip(".+#!?")
    if not text:
        return None
    candidates = [text]
    castle = text.lower().replace("0", "o")
    if castle in ("o-o", "oo"):
        candidates.insert(0, "O-O")
    elif castle in ("o-o-o", "ooo"):
        candidates.insert(0, "O-O-O")
    elif castle in ("left", "right", "castle left", "castle right", "castles left", "castles right"):
        return spoken_to_move(board, "castle " + castle.split()[-1])
    elif text[0].lower() in "kqrbn":
        # Pawn moves like "bxc3" stay as typed first; then try the piece reading.
        candidates.append(text[0].upper() + text[1:])
    if len(text) >= 2 and text[0].isupper() and text[0] != "B":
        candidates.append(text[0].lower() + text[1:])
    for cand in candidates:
        try:
            return board.parse_san(cand)
        except ValueError:
            pass
    try:
        move = chess.Move.from_uci(text.lower())
        if move in board.legal_moves:
            return move
    except ValueError:
        pass
    return spoken_to_move(board, raw)
