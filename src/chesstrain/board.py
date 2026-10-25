"""Terminal board rendering (Nerd Font icons or ASCII; ANSI colours optional)."""

from __future__ import annotations

import os
import sys

import chess

# Solid Font Awesome chess icons from Nerd Fonts (JetBrainsMono Nerd Font has them), one cell,
# coloured per side. --ascii (or pieces = "ascii") for fonts without them.
GLYPHS = {"k": "", "q": "", "r": "", "b": "", "n": "", "p": ""}

LIGHT_BG = "\033[48;5;180m"
DARK_BG = "\033[48;5;137m"
LAST_LIGHT_BG = "\033[48;5;186m"
LAST_DARK_BG = "\033[48;5;143m"
WHITE_FG = "\033[38;5;231m"
BLACK_FG = "\033[38;5;16m"
RESET = "\033[0m"


def use_colour() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def render(
    board: chess.Board,
    orientation: chess.Color = chess.WHITE,
    lastmove: chess.Move | None = None,
    ascii: bool = False,
    colour: bool | None = None,
    small: bool = True,
    pieces: str = "icons",
) -> str:
    """Small squares are 3 columns x 1 row (zoom the terminal to taste); large
    squares are 7 x 3. pieces: "icons" or "ascii"."""
    colour = use_colour() if colour is None else colour
    if ascii:
        pieces = "ascii"
    if lastmove is None and board.move_stack:
        lastmove = board.peek()
    highlight = {lastmove.from_square, lastmove.to_square} if lastmove else set()
    width, height = (3, 1) if small else (7, 3)
    pad = " " * (width // 2)

    ranks = range(7, -1, -1) if orientation == chess.WHITE else range(8)
    files = range(8) if orientation == chess.WHITE else range(7, -1, -1)
    lines = []
    for rank in ranks:
        for sub in range(height):
            middle = sub == height // 2
            row = [f"{rank + 1} " if middle else "  "]
            for file in files:
                sq = chess.square(file, rank)
                piece = board.piece_at(sq)
                light = (file + rank) % 2 == 1
                if piece is not None and middle:
                    # Without colour, letters are the only unambiguous way to show sides.
                    sym = piece.symbol() if (pieces == "ascii" or not colour) else GLYPHS[piece.symbol().lower()]
                    cell = pad + sym + pad
                elif not colour and middle:
                    cell = pad + ("*" if sq in highlight else ("." if not light else " ")) + pad
                else:
                    cell = " " * width
                if colour:
                    if sq in highlight:
                        bg = LAST_LIGHT_BG if light else LAST_DARK_BG
                    else:
                        bg = LIGHT_BG if light else DARK_BG
                    fg = WHITE_FG if piece and piece.color == chess.WHITE else BLACK_FG
                    row.append(f"{bg}{fg}{cell}{RESET}")
                else:
                    row.append(cell)
            lines.append("".join(row))
    lines.append("  " + "".join(f"{pad}{chess.FILE_NAMES[f]}{pad}" for f in files))
    return "\n".join(lines)


def move_list(board: chess.Board, moves: list[chess.Move] | None = None) -> str:
    """SAN move list from the start position, e.g. '1.e4 d6 2.d4 Nf6'."""
    moves = list(board.move_stack) if moves is None else moves
    tmp = chess.Board()
    return tmp.variation_san(moves) if moves else "(start position)"
