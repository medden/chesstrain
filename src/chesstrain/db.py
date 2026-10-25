"""SQLite progress store: FSRS cards, review log, imported games, gaps."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import fsrs

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    system      TEXT NOT NULL,
    key         TEXT NOT NULL,
    fsrs        TEXT NOT NULL,
    due         TEXT NOT NULL,
    reps        INTEGER NOT NULL DEFAULT 0,
    lapses      INTEGER NOT NULL DEFAULT 0,
    first_seen  TEXT NOT NULL,
    PRIMARY KEY (system, key)
);
CREATE TABLE IF NOT EXISTS reviews (
    id      INTEGER PRIMARY KEY,
    ts      TEXT NOT NULL,
    system  TEXT NOT NULL,
    key     TEXT NOT NULL,
    rating  INTEGER NOT NULL,
    mode    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS games (
    id            TEXT PRIMARY KEY,
    player        TEXT NOT NULL,          -- "me" or a creator alias
    system        TEXT,
    colour        TEXT,
    white         TEXT,
    black         TEXT,
    result        TEXT,
    date          TEXT,
    deviation_ply INTEGER,
    deviation_by  TEXT,                   -- "me" | "opponent" | NULL (stayed in book)
    imported_at   TEXT NOT NULL,
    pgn           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gaps (
    system    TEXT NOT NULL,
    key       TEXT NOT NULL,
    move_uci  TEXT NOT NULL,
    move_san  TEXT NOT NULL,
    count     INTEGER NOT NULL DEFAULT 0,
    last_game TEXT,
    PRIMARY KEY (system, key, move_uci)
);
CREATE TABLE IF NOT EXISTS guesses (
    id       INTEGER PRIMARY KEY,
    ts       TEXT NOT NULL,
    player   TEXT NOT NULL,
    game_id  TEXT NOT NULL,
    system   TEXT,
    correct  INTEGER NOT NULL,
    total    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_ts ON reviews (ts);
CREATE INDEX IF NOT EXISTS cards_due ON cards (due);
"""

RATING_NAMES = {fsrs.Rating.Again: "again", fsrs.Rating.Hard: "hard", fsrs.Rating.Good: "good"}


def now_utc() -> datetime:
    return datetime.now(UTC)


@dataclass
class CardRow:
    system: str
    key: str
    card: fsrs.Card
    reps: int
    lapses: int
    first_seen: datetime

    @property
    def due(self) -> datetime:
        return self.card.due


class Progress:
    def __init__(self, path: Path | str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.scheduler = fsrs.Scheduler(enable_fuzzing=False)

    def close(self) -> None:
        self.conn.close()

    # -- cards --------------------------------------------------------------

    def _row(self, r: sqlite3.Row) -> CardRow:
        return CardRow(
            system=r["system"],
            key=r["key"],
            card=fsrs.Card.from_dict(json.loads(r["fsrs"])),
            reps=r["reps"],
            lapses=r["lapses"],
            first_seen=datetime.fromisoformat(r["first_seen"]),
        )

    def get(self, system: str, key: str) -> CardRow | None:
        r = self.conn.execute("SELECT * FROM cards WHERE system=? AND key=?", (system, key)).fetchone()
        return self._row(r) if r else None

    def cards(self, system: str | None = None) -> list[CardRow]:
        if system:
            rows = self.conn.execute("SELECT * FROM cards WHERE system=?", (system,))
        else:
            rows = self.conn.execute("SELECT * FROM cards")
        return [self._row(r) for r in rows]

    def due(self, system: str | None = None, at: datetime | None = None) -> list[CardRow]:
        at = at or now_utc()
        sql = "SELECT * FROM cards WHERE due <= ?"
        args: list = [at.isoformat()]
        if system:
            sql += " AND system = ?"
            args.append(system)
        sql += " ORDER BY due"
        return [self._row(r) for r in self.conn.execute(sql, args)]

    def new_today(self, system: str, at: datetime | None = None) -> int:
        at = (at or now_utc()).astimezone()
        start = at.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        r = self.conn.execute(
            "SELECT COUNT(*) FROM cards WHERE system=? AND first_seen >= ?", (system, start.isoformat())
        ).fetchone()
        return r[0]

    def review(self, system: str, key: str, rating: fsrs.Rating, mode: str, at: datetime | None = None) -> CardRow:
        at = at or now_utc()
        existing = self.get(system, key)
        card = existing.card if existing else fsrs.Card()
        card, _ = self.scheduler.review_card(card, rating, review_datetime=at)
        reps = (existing.reps if existing else 0) + 1
        lapses = (existing.lapses if existing else 0) + (1 if rating == fsrs.Rating.Again else 0)
        first_seen = existing.first_seen if existing else at
        self.conn.execute(
            "INSERT OR REPLACE INTO cards (system, key, fsrs, due, reps, lapses, first_seen) VALUES (?,?,?,?,?,?,?)",
            (system, key, json.dumps(card.to_dict()), card.due.isoformat(), reps, lapses, first_seen.isoformat()),
        )
        self.conn.execute(
            "INSERT INTO reviews (ts, system, key, rating, mode) VALUES (?,?,?,?,?)",
            (at.isoformat(), system, key, int(rating), mode),
        )
        self.conn.commit()
        return CardRow(system, key, card, reps, lapses, first_seen)

    def make_due(self, system: str, key: str, at: datetime | None = None) -> None:
        """Pull a card forward so the next review shows it (used for mistakes in real games)."""
        row = self.get(system, key)
        if row is None:
            return
        row.card.due = at or now_utc()
        self.conn.execute(
            "UPDATE cards SET fsrs=?, due=? WHERE system=? AND key=?",
            (json.dumps(row.card.to_dict()), row.card.due.isoformat(), system, key),
        )
        self.conn.commit()

    def retrievability(self, row: CardRow, at: datetime | None = None) -> float:
        return self.scheduler.get_card_retrievability(row.card, current_datetime=at or now_utc())

    def accuracy(self, system: str, days: int, at: datetime | None = None) -> tuple[int, int]:
        """(first-try correct, total) training reviews in the last `days` days."""
        since = (at or now_utc()) - timedelta(days=days)
        r = self.conn.execute(
            "SELECT SUM(rating = ?), COUNT(*) FROM reviews WHERE system=? AND ts >= ? AND mode != 'game'",
            (int(fsrs.Rating.Good), system, since.isoformat()),
        ).fetchone()
        return (r[0] or 0, r[1] or 0)

    # -- games & gaps -------------------------------------------------------

    def has_game(self, game_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM games WHERE id=?", (game_id,)).fetchone() is not None

    def add_game(self, **fields) -> None:
        fields.setdefault("imported_at", now_utc().isoformat())
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        self.conn.execute(f"INSERT OR REPLACE INTO games ({cols}) VALUES ({marks})", list(fields.values()))
        self.conn.commit()

    def games(self, player: str, system: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM games WHERE player=?"
        args: list = [player]
        if system:
            sql += " AND system=?"
            args.append(system)
        return list(self.conn.execute(sql + " ORDER BY date DESC", args))

    def add_gap(self, system: str, key: str, move_uci: str, move_san: str, game_id: str) -> None:
        self.conn.execute(
            """INSERT INTO gaps (system, key, move_uci, move_san, count, last_game) VALUES (?,?,?,?,1,?)
               ON CONFLICT (system, key, move_uci) DO UPDATE SET count = count + 1, last_game = excluded.last_game""",
            (system, key, move_uci, move_san, game_id),
        )
        self.conn.commit()

    def gaps(self, system: str | None = None) -> list[sqlite3.Row]:
        if system:
            return list(self.conn.execute("SELECT * FROM gaps WHERE system=? ORDER BY count DESC", (system,)))
        return list(self.conn.execute("SELECT * FROM gaps ORDER BY count DESC"))

    def add_guess(self, player: str, game_id: str, system: str | None, correct: int, total: int) -> None:
        self.conn.execute(
            "INSERT INTO guesses (ts, player, game_id, system, correct, total) VALUES (?,?,?,?,?,?)",
            (now_utc().isoformat(), player, game_id, system, correct, total),
        )
        self.conn.commit()

    def guess_totals(self, player: str) -> tuple[int, int, int]:
        r = self.conn.execute(
            "SELECT COUNT(*), SUM(correct), SUM(total) FROM guesses WHERE player=?", (player,)
        ).fetchone()
        return (r[0] or 0, r[1] or 0, r[2] or 0)
