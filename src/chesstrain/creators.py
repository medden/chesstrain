"""Creator games: divergence report, guess-the-move, adopting lines."""

from __future__ import annotations

import io
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import chess
import chess.pgn

from .analysis import walk
from .board import move_list
from .db import Progress
from .moves import MoveSource, command_of, notation_help, parse_move
from .repertoire import Repertoire, read_games
from .session import Display

SCORE = {"1-0": (1.0, 0.0), "0-1": (0.0, 1.0), "1/2-1/2": (0.5, 0.5)}


@dataclass
class Divergence:
    system: str
    board: chess.Board  # position before the creator's move
    played: chess.Move
    expected: chess.Move
    games: list[tuple[str, chess.pgn.Game]] = field(default_factory=list)
    score: float = 0.0  # creator's points in those games

    @property
    def count(self) -> int:
        return len(self.games)


def _game(pgn: str) -> chess.pgn.Game:
    return chess.pgn.read_game(io.StringIO(pgn))


def divergences(
    reps: dict[str, Repertoire], progress: Progress, player: str, system: str | None = None
) -> list[Divergence]:
    """Where the creator, playing your system, chose a different move than your repertoire."""
    buckets: dict[tuple, Divergence] = {}
    for row in progress.games(player, system):
        if row["system"] not in reps or row["deviation_by"] != "me":
            continue
        game = _game(row["pgn"])
        m = walk(reps[row["system"]], game)
        if m.deviation_by != "me":
            continue
        k = (m.system, m.key, m.played.uci())
        d = buckets.get(k)
        if d is None:
            d = buckets[k] = Divergence(m.system, m.board, m.played, m.expected[0])
        d.games.append((row["id"], game))
        white_pts, black_pts = SCORE.get(row["result"] or "", (0.0, 0.0))
        d.score += white_pts if row["colour"] == "white" else black_pts
    return sorted(buckets.values(), key=lambda d: (-d.count, len(d.board.move_stack)))


def report_lines(
    divs: list[Divergence], reps: dict[str, Repertoire], alias: str, system: str | None = None
) -> list[str]:
    """Numbers are positions in the full list, so `adopt <n>` works after a filtered report."""
    numbered = [(i, d) for i, d in enumerate(divs, 1) if system is None or d.system == system]
    if not numbered:
        return [f"No divergences found for {alias} yet (fetch some games first, or they play your lines exactly)."]
    out = []
    by_system = defaultdict(list)
    for i, d in numbered:
        by_system[d.system].append((i, d))
    for system, items in by_system.items():
        out.append(f"\n{reps[system].system.name}")
        for i, d in items:
            b = d.board
            pct = 100 * d.score / d.count
            out.append(
                f"  [{i}] after {move_list(b)}: {alias} plays {b.san(d.played)} "
                f"({d.count} game{'s' if d.count != 1 else ''}, scored {pct:.0f}%) "
                f"— your repertoire: {b.san(d.expected)}"
            )
    out.append(f"\nAdopt one with: chesstrain adopt {alias} <number>")
    return out


def adopt_line(div: Divergence, extra_plies: int = 8) -> list[chess.Move]:
    """Moves from the start through the creator's choice and a few plies beyond,
    taken from the first example game."""
    _, game = div.games[0]
    moves = list(game.mainline_moves())
    stop = len(div.board.move_stack) + 1 + extra_plies
    return moves[:stop]


def add_line_to_pgn(pgn_path: Path, moves: list[chess.Move], comment: str) -> None:
    """Merge a line into the first game of a repertoire PGN (as a variation)."""
    text = pgn_path.read_text() if pgn_path.exists() else ""
    games = read_games(text)
    game = games[0] if games else chess.pgn.Game()
    node = game
    branched = False
    for move in moves:
        existing = next((v for v in node.variations if v.move == move), None)
        if existing is None:
            node = node.add_variation(move)
            if not branched:
                node.comment = comment
                branched = True
        else:
            node = existing
    games = [game, *games[1:]]
    pgn_path.write_text("\n\n".join(str(g) for g in games) + "\n")


def guess_game(
    rep: Repertoire,
    game: chess.pgn.Game,
    colour: chess.Color,
    alias: str,
    source: MoveSource,
    display: Display,
    max_moves: int = 20,
) -> tuple[int, int, bool]:
    """Walk a creator game; the user predicts the creator's moves. Returns (correct, total, quit)."""
    board = game.board()
    correct = total = 0
    h = game.headers
    display.say(f"\n=== Guess {alias}'s moves: {h.get('White')} vs {h.get('Black')} ({h.get('Date', '')}) ===")
    for move in game.mainline_moves():
        if board.fullmove_number > max_moves:
            break
        if board.turn != colour:
            display.say(f"  opponent plays {board.san(move)}")
            board.push(move)
            continue
        display.say("")
        display.say(move_list(board))
        display.board(board, colour)
        while True:
            text = source.next_input(board, f"{alias} plays? (? for help) ")
            if command_of(text) == "quit":
                return correct, total, True
            if command_of(text) == "help":
                display.say(notation_help(colour))
                continue
            if command_of(text) == "skip":
                guess = None
                break
            guess = parse_move(board, text)
            if guess is not None:
                break
            display.say(f"Couldn't read '{text}' as a legal move here.")
        actual = board.san(move)
        total += 1
        book = rep.moves_at(board)
        if guess == move:
            correct += 1
            display.say(f"✓ {actual}")
        else:
            shown = board.san(guess) if guess else "—"
            display.say(f"✗ you said {shown}; {alias} played {actual}")
        if book and move not in book:
            display.say(f"  (your repertoire has {board.san(book[0])} here)")
        board.push(move)
    display.say(f"\nScore for this game: {correct}/{total}")
    return correct, total, False


def pick_game(progress: Progress, alias: str, system: str | None, rng: random.Random | None = None):
    rows = [r for r in progress.games(alias, system) if r["system"]]
    if not rows:
        return None
    done = {r["game_id"] for r in progress.conn.execute("SELECT game_id FROM guesses WHERE player=?", (alias,))}
    fresh = [r for r in rows if r["id"] not in done] or rows
    return (rng or random).choice(fresh)
