"""Match real games against repertoires: where did the game leave book, and who left?"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import chess
import chess.pgn

from .repertoire import Repertoire, position_key


@dataclass
class Match:
    system: str
    colour: chess.Color
    book_plies: int  # plies played while still in the repertoire
    deviation_ply: int | None  # index of the first out-of-book move, None if game stayed in book
    deviation_by: str | None  # "me" | "opponent" | None
    key: str | None  # position before the deviating move
    played: chess.Move | None
    expected: list[chess.Move]
    board: chess.Board | None  # position before the deviating move


def game_id(game: chess.pgn.Game) -> str:
    link = game.headers.get("Link") or game.headers.get("Site", "")
    if link.startswith("http") and ("chess.com" in link or "lichess.org" in link):
        return link
    raw = "|".join(
        [game.headers.get(h, "") for h in ("White", "Black", "Date", "UTCTime", "Result")]
        + [m.uci() for m in game.mainline_moves()]
    )
    return "sha1:" + hashlib.sha1(raw.encode()).hexdigest()


def player_colour(game: chess.pgn.Game, names: set[str]) -> chess.Color | None:
    names = {n.lower() for n in names}
    if game.headers.get("White", "").lower() in names:
        return chess.WHITE
    if game.headers.get("Black", "").lower() in names:
        return chess.BLACK
    return None


def is_bullet(game: chess.pgn.Game) -> bool:
    tc = game.headers.get("TimeControl", "")
    if not tc or tc == "-" or "/" in tc:  # unknown or daily/correspondence
        return False
    try:
        base, _, inc = tc.partition("+")
        estimate = int(base) + 40 * int(inc or 0)
    except ValueError:
        return False
    return estimate < 180


def is_standard(game: chess.pgn.Game) -> bool:
    variant = game.headers.get("Variant", "Standard").lower()
    return variant in ("standard", "chess", "") and "FEN" not in game.headers


def walk(rep: Repertoire, game: chess.pgn.Game) -> Match:
    board = game.board()
    colour = rep.system.colour
    ply = 0
    for move in game.mainline_moves():
        expected = rep.moves_at(board)
        if not expected:  # end of prepared line — stayed in book
            return Match(rep.system.key, colour, ply, None, None, None, None, [], None)
        if move not in expected:
            who = "me" if board.turn == colour else "opponent"
            return Match(rep.system.key, colour, ply, ply, who, position_key(board), move, expected, board.copy())
        board.push(move)
        ply += 1
    return Match(rep.system.key, colour, ply, None, None, None, None, [], None)


def my_book_moves(match: Match) -> int:
    """How many of the player's own moves matched the repertoire."""
    first = 0 if match.colour == chess.WHITE else 1
    return max(0, (match.book_plies - first + 1) // 2)


def detect(reps: dict[str, Repertoire], game: chess.pgn.Game, colour: chess.Color) -> Match | None:
    """Pick the system (for this colour) the game followed longest.

    Requires at least one of the player's own moves to be in book, so e.g.
    1.e4 c5 isn't attributed to the Pirc just because 1.e4 is in that tree."""
    same_colour = [r for r in reps.values() if r.system.colour == colour]
    best: tuple[int, int, Match] | None = None
    for rep in same_colour:
        m = walk(rep, game)
        if my_book_moves(m) == 0:
            continue
        # The player's book moves must reach a position specific to this system:
        # 1.d4 alone doesn't say Jobava or Colle.
        others = [r for r in same_colour if r is not rep]
        if not _distinctive(rep, others, game, colour, m.book_plies):
            continue
        # Tie-break (e.g. opponent left book on move 1): which system's typical
        # moves does the player actually make in the first dozen moves?
        score = (m.book_plies, _style_overlap(rep, game, colour))
        if best is None or score > best[:2]:
            best = (*score, m)
    return best[2] if best else None


def shared_gap(reps: dict[str, Repertoire], game: chess.pgn.Game, colour: chess.Color) -> list[Match]:
    """Opponent left book while still in a prefix shared by several systems
    (e.g. 1.d4 e5): that's a gap in each of them."""
    matches = [walk(r, game) for r in reps.values() if r.system.colour == colour]
    matches = [m for m in matches if my_book_moves(m) > 0 and m.deviation_by == "opponent"]
    if matches and len({(m.key, m.played) for m in matches}) == 1:
        return matches
    return []


def _distinctive(rep: Repertoire, others: list[Repertoire], game: chess.pgn.Game, colour: chess.Color, plies: int) -> bool:
    board = game.board()
    last_mine = None
    for i, move in enumerate(game.mainline_moves()):
        if i >= plies:
            break
        mover = board.turn
        board.push(move)
        if mover == colour:
            last_mine = position_key(board)
    return last_mine is not None and not any(last_mine in o.nodes for o in others)


def _style_overlap(rep: Repertoire, game: chess.pgn.Game, colour: chess.Color, plies: int = 24) -> int:
    mine = {m.uci() for n in rep.nodes.values() if n.turn == colour for m in n.moves}
    board = game.board()
    hits = 0
    for i, move in enumerate(game.mainline_moves()):
        if i >= plies:
            break
        if board.turn == colour and move.uci() in mine:
            hits += 1
        board.push(move)
    return hits
