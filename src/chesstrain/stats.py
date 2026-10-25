"""Progress summary per system."""

from __future__ import annotations

import fsrs

from .board import move_list
from .db import Progress, now_utc
from .repertoire import Repertoire


def stats_lines(reps: dict[str, Repertoire], progress: Progress) -> list[str]:
    now = now_utc()
    out = []
    for key, rep in reps.items():
        cards = set(rep.card_keys())
        rows = [r for r in progress.cards(key) if r.key in cards]
        seen = {r.key for r in rows}
        learned = sum(1 for r in rows if r.card.state == fsrs.State.Review)
        learning = len(rows) - learned
        due = sum(1 for r in rows if r.due <= now)
        g7, t7 = progress.accuracy(key, 7)
        g30, t30 = progress.accuracy(key, 30)
        pct = lambda g, t: f"{100 * g / t:.0f}% of {t}" if t else "—"  # noqa: E731
        out.append(f"\n{rep.system.name} ({rep.system.colour_name})")
        out.append(
            f"  positions: {len(cards)} total · {learned} learned · {learning} learning · "
            f"{len(cards) - len(seen)} new · {due} due now"
        )
        out.append(f"  first-try accuracy: 7 days {pct(g7, t7)} · 30 days {pct(g30, t30)}")
        weak = sorted(
            (r for r in rows if r.lapses),
            key=lambda r: (-r.lapses, progress.retrievability(r, now)),
        )[:5]
        if weak:
            out.append("  weakest positions:")
            for r in weak:
                b = rep.board_for(r.key)
                answer = b.san(rep.moves_at(b)[0])
                out.append(f"    {r.lapses}× wrong — after {move_list(b)} → {answer}")
        gaps = [g for g in progress.gaps(key)][:5]
        if gaps:
            out.append("  gaps from your games (opponent moves you haven't prepared):")
            for g in gaps:
                b = rep.board_for(g["key"])
                where = move_list(b) if b else "(position no longer in repertoire)"
                out.append(f"    {g['count']}× after {where} … {g['move_san']}")
    return out
