"""Build repertoire PGNs from seed lines, optionally checking coverage against
the Lichess opening explorer.

Seed file format (scripts/seeds/<system>.txt): one line per row, SAN moves with
or without move numbers. Lines starting with '#' are comments. The first line
that reaches a position sets the mainline there.

    python scripts/build_repertoire.py              # build all PGNs, report stats
    python scripts/build_repertoire.py --explorer   # also report coverage gaps
                                                    # (needs LICHESS_TOKEN or config.local.toml)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from hashlib import sha1
from pathlib import Path

import chess
import chess.pgn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from chesstrain.config import load_config  # noqa: E402
from chesstrain.repertoire import Repertoire, load_system, position_key  # noqa: E402

SEEDS = ROOT / "scripts" / "seeds"
CACHE = ROOT / "scripts" / ".explorer-cache"
EXPLORER = "https://explorer.lichess.ovh/lichess"


def parse_seed(path: Path) -> list[list[str]]:
    lines = []
    for n, raw in enumerate(path.read_text().splitlines(), 1):
        raw = raw.split("#", 1)[0].strip()
        if not raw:
            continue
        tokens = [t for t in raw.split() if not t.rstrip(".").isdigit()]
        tokens = [t.split(".")[-1] for t in tokens]  # "1.e4" -> "e4"
        lines.append((n, [t for t in tokens if t]))
    return lines


def build_game(system_name: str, seed_lines) -> chess.pgn.Game:
    game = chess.pgn.Game()
    game.headers["Event"] = f"{system_name} repertoire"
    game.headers["Site"] = "chesstrain baseline"
    for key in ("Date", "Round", "White", "Black", "Result"):
        del game.headers[key]
    for lineno, sans in seed_lines:
        node = game
        board = chess.Board()
        for san in sans:
            try:
                move = board.parse_san(san)
            except ValueError as e:
                raise SystemExit(
                    f"{system_name} seed line {lineno}: illegal move {san!r} after "
                    f"{chess.Board().variation_san(board.move_stack)}: {e}"
                ) from e
            existing = next((v for v in node.variations if v.move == move), None)
            node = existing or node.add_variation(move)
            board.push(move)
    return game


# -- explorer ---------------------------------------------------------------


def explorer(fen: str, token: str) -> dict:
    import httpx

    CACHE.mkdir(parents=True, exist_ok=True)
    cache = CACHE / (sha1(fen.encode()).hexdigest() + ".json")
    if cache.exists():
        return json.loads(cache.read_text())
    params = {"fen": fen, "speeds": "blitz,rapid,classical", "ratings": "1400,1600,1800,2000", "moves": 12}
    for _attempt in range(5):
        r = httpx.get(EXPLORER, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if r.status_code == 429:
            time.sleep(60)
            continue
        r.raise_for_status()
        data = r.json()
        cache.write_text(json.dumps(data))
        time.sleep(0.5)
        return data
    raise SystemExit("explorer: rate limited repeatedly")


def coverage(rep: Repertoire, token: str, max_replies: int = 3, target: float = 0.8, min_games: int = 200):
    """Walk opponent-to-move positions; report popular replies the tree lacks,
    and my moves that are rare at club level."""
    colour = rep.system.colour
    gaps, rare = [], []
    start = position_key(chess.Board())
    nodes = [n for n in rep.nodes.values() if n.moves and (n.path or n.key == start)]
    for done, node in enumerate(nodes, 1):
        # Progress on stderr, flushed: the run takes minutes and stdout may be redirected.
        print(f"\r   explorer {done}/{len(nodes)}", end="", file=sys.stderr, flush=True)
        board = rep.board_for(node.key)
        data = explorer(board.fen(), token)
        moves = data.get("moves", [])
        total = sum(m["white"] + m["draws"] + m["black"] for m in moves)
        if total < min_games:
            continue
        # Compare by SAN: the explorer writes castling king-takes-rook (e1h1), python-chess as e1g1.
        if node.turn != colour:
            if not node.path:
                continue  # White's first move vs a Black defence chooses the system, not a gap
            have = {board.san(m) for m in node.moves}
            cum = 0.0
            for m in moves[:max_replies]:
                share = (m["white"] + m["draws"] + m["black"]) / total
                if cum >= target:
                    break
                cum += share
                if m["san"] not in have:
                    gaps.append((board.copy(), m["san"], share, total))
        else:
            for mine in node.moves:
                row = next((m for m in moves if m["san"] == board.san(mine)), None)
                share = (row["white"] + row["draws"] + row["black"]) / total if row else 0.0
                if share < 0.03:
                    rare.append((board.copy(), board.san(mine), share, total))
    print(file=sys.stderr)
    return gaps, rare


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("systems", nargs="*", help="system folders to build (default: all seeds)")
    ap.add_argument("--explorer", action="store_true", help="check coverage with the Lichess explorer")
    ap.add_argument("--force", action="store_true", help="overwrite PGNs that were edited by hand")
    args = ap.parse_args()

    cfg = load_config(ROOT)
    seeds = sorted(SEEDS.glob("*.txt"))
    if args.systems:
        seeds = [s for s in seeds if s.stem in args.systems]
    for seed in seeds:
        folder = cfg.repertoire_dir / seed.stem
        system = load_system(folder)
        game = build_game(system.name, parse_seed(seed))
        out = folder / "repertoire.pgn"
        text = str(game) + "\n"
        marker = folder / ".built-hash"
        hand_edited = (
            out.exists() and marker.exists() and sha1(out.read_bytes()).hexdigest() != marker.read_text().strip()
        )
        if hand_edited and not args.force:
            print(f"! {out.relative_to(ROOT)} was edited by hand; skipping (use --force to overwrite)")
            continue
        out.write_text(text)
        marker.write_text(sha1(text.encode()).hexdigest())
        rep = Repertoire(system, [game])
        cards = rep.card_keys()
        print(f"{system.name}: {len(cards)} positions to learn → {out.relative_to(ROOT)}", flush=True)
        for key, moves in rep.alternatives():
            b = rep.board_for(key)
            print(
                f"   note: alternatives at {chess.Board().variation_san(b.move_stack)}: "
                + ", ".join(b.san(m) for m in moves)
            )
        if args.explorer:
            if not cfg.lichess_token:
                raise SystemExit("--explorer needs LICHESS_TOKEN or [lichess] token in config.local.toml")
            gaps, rare = coverage(rep, cfg.lichess_token)
            for b, san, share, total in sorted(gaps, key=lambda g: -g[2] * g[3]):
                print(
                    f"   GAP  {chess.Board().variation_san(b.move_stack)} … {san} "
                    f"({share:.0%} of {total} games) not covered"
                )
            for b, san, share, total in rare:
                print(
                    f"   RARE {chess.Board().variation_san(b.move_stack)} … your {san} is {share:.1%} of {total} games"
                )


if __name__ == "__main__":
    main()
