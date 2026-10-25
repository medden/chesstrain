"""Importing games: your own (feeds review + gaps) and creators' (feeds reports)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import chess
import chess.pgn
import fsrs

from .analysis import detect, game_id, is_bullet, is_standard, player_colour, shared_gap
from .board import move_list
from .db import Progress
from .repertoire import Repertoire


@dataclass
class ImportSummary:
    counts: Counter = field(default_factory=Counter)
    systems: Counter = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)

    def header(self) -> list[str]:
        c = self.counts
        out = [
            f"Games read: {c['read']}  imported: {c['imported']}  already had: {c['duplicate']}",
            f"Skipped: {c['bullet']} bullet, {c['variant']} variant/odd start, "
            f"{c['not_player']} not involving the player, {c['unmatched']} not in any repertoire",
        ]
        if self.systems:
            out.append("By system: " + ", ".join(f"{k} {v}" for k, v in self.systems.most_common()))
        if c["dev_me"] or c["dev_opp"] or c["in_book"]:
            out.append(
                f"Left book: {c['dev_me']} by the player, {c['dev_opp']} by the opponent; "
                f"{c['in_book']} stayed in book to the end of the prepared line"
            )
        return out


def _describe(game: chess.pgn.Game) -> str:
    h = game.headers
    return f"{h.get('White', '?')}–{h.get('Black', '?')} {h.get('Date', '')}".strip()


def process_game(
    game: chess.pgn.Game,
    reps: dict[str, Repertoire],
    progress: Progress,
    player: str,
    names: set[str],
    skip_bullet: bool,
    summary: ImportSummary,
) -> None:
    """player is "me" or a creator alias. names: usernames identifying the player."""
    summary.counts["read"] += 1
    gid = game_id(game)
    if progress.has_game(gid):
        summary.counts["duplicate"] += 1
        return
    if not is_standard(game):
        summary.counts["variant"] += 1
        return
    if skip_bullet and is_bullet(game):
        summary.counts["bullet"] += 1
        return
    colour = player_colour(game, names)
    if colour is None:
        summary.counts["not_player"] += 1
        return
    match = detect(reps, game, colour)
    record = dict(
        id=gid,
        player=player,
        colour="white" if colour == chess.WHITE else "black",
        white=game.headers.get("White"),
        black=game.headers.get("Black"),
        result=game.headers.get("Result"),
        date=game.headers.get("UTCDate") or game.headers.get("Date"),
        pgn=str(game),
    )
    if match is None:
        shared = shared_gap(reps, game, colour) if player == "me" else []
        progress.add_game(**record, system=None, deviation_ply=None, deviation_by=None)
        if not shared:
            summary.counts["unmatched"] += 1
            return
        summary.counts["imported"] += 1
        summary.counts["dev_opp"] += 1
        m = shared[0]
        played = m.board.san(m.played)
        for s in shared:
            progress.add_gap(s.system, s.key, s.played.uci(), played, gid)
        names = ", ".join(reps[s.system].system.name for s in shared)
        summary.notes.append(
            f"  ? {_describe(game)}: after {move_list(m.board)} opponent played {played} — not prepared in {names} (gap)"
        )
        return

    rep = reps[match.system]
    summary.counts["imported"] += 1
    summary.systems[rep.system.name] += 1
    progress.add_game(**record, system=match.system, deviation_ply=match.deviation_ply, deviation_by=match.deviation_by)
    if match.deviation_by is None:
        summary.counts["in_book"] += 1
        return
    board = match.board
    played = board.san(match.played)
    expected = board.san(match.expected[0])
    where = move_list(board)
    if match.deviation_by == "me":
        summary.counts["dev_me"] += 1
        if player == "me":
            progress.review(match.system, match.key, fsrs.Rating.Again, mode="game")
            progress.make_due(match.system, match.key)
            summary.notes.append(
                f"  ✗ {_describe(game)}: after {where} you played {played}; repertoire: {expected} → queued for review"
            )
    else:
        summary.counts["dev_opp"] += 1
        if player == "me":
            progress.add_gap(match.system, match.key, match.played.uci(), played, gid)
            summary.notes.append(f"  ? {_describe(game)}: after {where} opponent played {played} — not prepared (gap)")


def import_text(text: str, **kwargs) -> None:
    import io

    stream = io.StringIO(text)
    while True:
        try:
            game = chess.pgn.read_game(stream)
        except Exception as e:  # malformed PGN chunk
            kwargs["summary"].notes.append(f"  ! could not parse a game: {e}")
            continue
        if game is None:
            break
        if game.errors:
            kwargs["summary"].notes.append(f"  ! PGN problems in {_describe(game)}: {game.errors[0]}")
        process_game(game, **kwargs)
