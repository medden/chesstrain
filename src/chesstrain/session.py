"""Training sessions: drill (walk a line) and review (due positions)."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum

import chess
import fsrs

from .board import move_list, render
from .db import Progress, now_utc
from .moves import MoveSource, command_of, notation_help, parse_move
from .repertoire import Repertoire, position_key


class Outcome(Enum):
    GOOD = "good"
    HARD = "hard"
    AGAIN = "again"
    SKIP = "skip"
    NEXT = "next"  # abandon this line (drill) / position (review)
    QUIT = "quit"


RATING = {Outcome.GOOD: fsrs.Rating.Good, Outcome.HARD: fsrs.Rating.Hard, Outcome.AGAIN: fsrs.Rating.Again}


@dataclass
class Display:
    ascii: bool = False
    small: bool = True
    pieces: str = "icons"
    colour: bool | None = None
    out: list[str] | None = None  # capture output (tests); None prints
    window: object | None = None  # BoardWindow: shows the board graphically instead

    def _emit(self, text: str) -> None:
        if self.out is None:
            print(text)
        else:
            self.out.append(text)

    def say(self, text: str = "") -> None:
        self._emit(text)
        if self.window is not None:
            self.window.say(text)

    def verdict(self, ok: bool) -> None:
        """Right/wrong signal; the window flashes its board border."""
        if self.window is not None:
            self.window.verdict(ok)

    def board(self, board: chess.Board, orientation: chess.Color, force_terminal: bool = False) -> None:
        if self.window is not None:
            self.window.show(board, orientation, caption=move_list(board))
            if not force_terminal:
                return
        self._emit(
            render(board, orientation, ascii=self.ascii, colour=self.colour, small=self.small, pieces=self.pieces)
        )


@dataclass
class Tally:
    good: int = 0
    hard: int = 0
    again: int = 0
    skipped: int = 0

    def add(self, outcome: Outcome) -> None:
        if outcome == Outcome.GOOD:
            self.good += 1
        elif outcome == Outcome.HARD:
            self.hard += 1
        elif outcome == Outcome.AGAIN:
            self.again += 1
        elif outcome == Outcome.SKIP:
            self.skipped += 1

    @property
    def total(self) -> int:
        return self.good + self.hard + self.again

    def summary(self) -> str:
        if not self.total:
            return "No positions answered."
        pct = 100 * self.good / self.total
        return (
            f"Answered {self.total}: {self.good} correct first try ({pct:.0f}%), "
            f"{self.hard} with hint, {self.again} wrong" + (f", {self.skipped} skipped" if self.skipped else "")
        )


def _san(board: chess.Board, move: chess.Move) -> str:
    return board.san(move)


def _hint(board: chess.Board, move: chess.Move) -> str:
    piece = board.piece_at(move.from_square)
    name = chess.piece_name(piece.piece_type) if piece else "piece"
    return f"Hint: move your {name} on {chess.square_name(move.from_square)}."


def quiz(
    board: chess.Board,
    accepted: list[chess.Move],
    source: MoveSource,
    display: Display,
    orientation: chess.Color,
) -> tuple[Outcome, chess.Move | None]:
    """Ask for the repertoire move. accepted[0] is the mainline (preferred) move."""
    main = accepted[0]
    hinted = False
    while True:
        text = source.next_input(board, "Your move: ")
        cmd = command_of(text)
        if cmd == "quit":
            return Outcome.QUIT, None
        if cmd == "next":
            return Outcome.NEXT, None
        if cmd == "skip":
            display.say(f"Skipped. The move was {_san(board, main)}.")
            return Outcome.SKIP, main
        if cmd == "hint":
            hinted = True
            display.say(_hint(board, main))
            continue
        if cmd == "board":
            display.board(board, orientation, force_terminal=True)
            continue
        if cmd == "help":
            display.say(notation_help(orientation))
            continue
        move = parse_move(board, text)
        if move is None:
            display.say(f"Couldn't read '{text}' as a legal move here. Try again.")
            continue
        if move in accepted:
            display.verdict(True)
            if move != main:
                display.say(f"✓ {_san(board, move)} is in your repertoire (main line prefers {_san(board, main)}).")
            else:
                display.say(f"✓ {_san(board, move)}" + (" (with hint)" if hinted else ""))
            return (Outcome.HARD if hinted else Outcome.GOOD), move
        others = ", ".join(_san(board, m) for m in accepted[1:])
        display.verdict(False)
        display.say(
            f"✗ {_san(board, move)} isn't your repertoire move. Correct: {_san(board, main)}"
            + (f" (also: {others})" if others else "")
        )
        while True:
            text = source.next_input(board, f"Type {_san(board, main)} to lock it in: ")
            if command_of(text) == "quit":
                return Outcome.AGAIN, None
            retry = parse_move(board, text)
            if retry in accepted:
                return Outcome.AGAIN, retry
            display.say("Not quite — type the correct move.")


class Trainer:
    def __init__(
        self,
        rep: Repertoire,
        progress: Progress,
        source: MoveSource,
        display: Display,
        new_per_day: int = 10,
        rng: random.Random | None = None,
    ):
        self.rep = rep
        self.progress = progress
        self.source = source
        self.display = display
        self.new_per_day = new_per_day
        self.rng = rng or random.Random()
        self.tally = Tally()

    @property
    def system(self) -> str:
        return self.rep.system.key

    def _new_allowed(self) -> bool:
        return self.progress.new_today(self.system) < self.new_per_day

    def _card_weight(self, key: str, now, new_ok: bool) -> float:
        row = self.progress.get(self.system, key)
        if row is None:
            return 1.0 if new_ok else 0.0
        weight = 0.0
        if row.due <= now:
            weight += 2.0
        if row.lapses:
            weight += 0.5 * min(row.lapses, 4)
        return weight

    def choose_opponent_move(self, board: chess.Board) -> chess.Move:
        """Prefer the branch with the most due / new / weak positions."""
        moves = self.rep.moves_at(board)
        if len(moves) == 1:
            return moves[0]
        now = now_utc()
        new_ok = self._new_allowed()
        weights = []
        for move in moves:
            board.push(move)
            keys = self.rep.subtree_cards(position_key(board))
            board.pop()
            weights.append(sum(self._card_weight(k, now, new_ok) for k in keys))
        # Small floor keeps some variety even when nothing is due.
        weights = [w + 0.25 for w in weights]
        return self.rng.choices(moves, weights=weights)[0]

    def _ask(self, board: chess.Board, label: str) -> tuple[Outcome, chess.Move | None]:
        rep = self.rep
        key = position_key(board)
        orientation = rep.system.colour
        self.display.say("")
        self.display.say(f"{label}  {move_list(board)}")
        self.display.board(board, orientation)
        outcome, move = quiz(board, rep.moves_at(board), self.source, self.display, orientation)
        self.tally.add(outcome)
        if outcome in RATING:
            row = self.progress.review(self.system, key, RATING[outcome], mode="train")
            days = (row.due - now_utc()).total_seconds() / 86400
            when = f"{days:.0f} day(s)" if days >= 1 else f"{max(1, round(days * 1440))} min"
            self.display.say(f"  next review in {when}")
        return outcome, move

    def drill_line(self, start_after: int = 0, every: int = 1) -> bool:
        """Play one line from the start. Returns False if the user quit."""
        rep = self.rep
        board = chess.Board()
        my_index = 0
        while True:
            moves = rep.moves_at(board)
            if not moves:
                self.display.say("— end of prepared line —")
                return True
            if rep.is_my_turn(board):
                my_index += 1
                ask = my_index > start_after and (my_index - start_after - 1) % every == 0
                if ask:
                    known = self.progress.get(self.system, position_key(board)) is not None
                    if not known and not self._new_allowed():
                        self.display.say(
                            f"Daily limit of {self.new_per_day} new positions reached for "
                            f"{rep.system.name}; ending this line."
                        )
                        return True
                    label = "[NEW]" if not known else "[drill]"
                    outcome, move = self._ask(board, label)
                    if outcome == Outcome.QUIT:
                        return False
                    if outcome == Outcome.NEXT:
                        self.display.say("— on to the next line —")
                        return True
                    move = move or moves[0]
                else:
                    move = moves[0]
            else:
                move = self.choose_opponent_move(board)
            board.push(move)

    def drill(self, lines: int = 5, start_after: int = 0, every: int = 1) -> Tally:
        for i in range(lines):
            self.display.say(f"\n=== {self.rep.system.name}: line {i + 1}/{lines} ===")
            if not self.drill_line(start_after, every):
                break
        return self.tally


def review(
    reps: dict[str, Repertoire],
    progress: Progress,
    source: MoveSource,
    display: Display,
    system: str | None = None,
    limit: int | None = None,
) -> Tally:
    tally = Tally()
    trainers = {k: Trainer(r, progress, source, display) for k, r in reps.items()}
    for trainer in trainers.values():
        trainer.tally = tally

    def reviewable(card) -> chess.Board | None:
        rep = reps.get(card.system)
        board = rep.board_for(card.key) if rep else None
        return board if board is not None and rep.moves_at(board) else None

    asked = 0
    quit_ = False
    skipped: set[tuple[str, str]] = set()
    # Re-query after each pass: cards answered wrong come back once their
    # short learning step elapses, within the same sitting.
    while not quit_ and (limit is None or asked < limit):
        due = [
            (c, b)
            for c in progress.due(system)
            if (c.system, c.key) not in skipped and (b := reviewable(c)) is not None
        ]
        if not due:
            break
        for card, board in due:
            if limit is not None and asked >= limit:
                break
            rep = reps[card.system]
            outcome, _ = trainers[card.system]._ask(board, f"[review · {rep.system.name}]")
            asked += 1
            if outcome == Outcome.QUIT:
                quit_ = True
                break
            if outcome in (Outcome.SKIP, Outcome.NEXT):
                skipped.add((card.system, card.key))
    soon = [c for c in progress.due(system, now_utc() + timedelta(minutes=15)) if reviewable(c) is not None]
    display.say("")
    if asked == 0:
        display.say("Nothing due right now. 🎉 Try `chesstrain drill <system>` to learn new positions.")
    display.say(tally.summary())
    if soon:
        display.say(f"{len(soon)} position(s) come due again within 15 minutes — run review again shortly.")
    return tally
