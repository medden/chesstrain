"""chesstrain command line."""

from __future__ import annotations

import argparse
import random
import shutil
import sys

import chess

from . import creators as cr
from .config import Config, load_config
from .db import Progress
from .fetch import FetchError, chesscom_games, lichess_games
from .games import ImportSummary, import_text
from .moves import CliMoveSource
from .repertoire import load_all, resolve_system
from .session import Display, Trainer, review
from .stats import stats_lines


def _context(args) -> tuple[Config, dict, Progress, Display]:
    cfg = load_config()
    reps = load_all(cfg.repertoire_dir)
    progress = Progress(cfg.db_path)
    display = Display(
        ascii=getattr(args, "ascii", False),
        small=(cfg.small_board or getattr(args, "small", False)) and not getattr(args, "large", False),
        pieces=getattr(args, "pieces", None) or cfg.pieces,
    )
    use_window = (cfg.window or getattr(args, "window", False)) and not getattr(args, "no_window", False)
    if use_window and hasattr(args, "window"):
        from .window import BoardWindow

        display.window = BoardWindow(cfg.window_port)
        url = display.window.start()
        print(f"Board window: {url}")
    return cfg, reps, progress, display


def _source(display: Display):
    """Terminal input, plus board clicks/buttons when the window is open."""
    if display.window is not None:
        from .window import WindowMoveSource

        return WindowMoveSource(display.window)
    return CliMoveSource()


def _system(reps, name: str):
    rep = resolve_system(reps, name)
    if rep is None:
        sys.exit(f"Unknown system '{name}'. Choose from: {', '.join(reps)}")
    return rep


def cmd_systems(args) -> None:
    _cfg, reps, progress, _ = _context(args)
    for key, rep in reps.items():
        cards = rep.card_keys()
        seen = {r.key for r in progress.cards(key)}
        due = len(progress.due(key))
        print(
            f"{key:15} {rep.system.name:20} {rep.system.colour_name:5}  "
            f"{len(cards):4} positions · {len(seen & set(cards)):4} seen · {due} due"
        )


def cmd_drill(args) -> None:
    cfg, reps, progress, display = _context(args)
    new = args.new or cfg.new_per_day
    source = _source(display)
    if args.system.lower() in ("all", "random", "mix"):
        trainers = [Trainer(r, progress, source, display, new_per_day=new) for r in reps.values()]
        tally = trainers[0].tally
        for t in trainers:
            t.tally = tally
        display.say("Drilling a random system each line. Say or type ? for help.")
        rng = random.Random()
        for i in range(args.lines):
            t = rng.choice(trainers)
            display.say(f"\n=== line {i + 1}/{args.lines}: {t.rep.system.name} as {t.rep.system.colour_name} ===")
            if not t.drill_line(args.start, args.every):
                break
    else:
        rep = _system(reps, args.system)
        trainer = Trainer(rep, progress, source, display, new_per_day=new)
        display.say(f"Drilling {rep.system.name} as {rep.system.colour_name}. Say or type ? for help.")
        tally = trainer.drill(lines=args.lines, start_after=args.start, every=args.every)
    display.say("")
    display.say(tally.summary())


def cmd_review(args) -> None:
    _cfg, reps, progress, display = _context(args)
    system = _system(reps, args.system).system.key if args.system else None
    review(reps, progress, _source(display), display, system=system, limit=args.limit)


def cmd_stats(args) -> None:
    cfg, reps, progress, _ = _context(args)
    for line in stats_lines(reps, progress):
        print(line)
    for p in cfg.players:
        n, good, total = progress.guess_totals(p.alias)
        if n:
            print(f"\nGuess-the-move vs {p.alias}: {good}/{total} ({100 * good / total:.0f}%) over {n} games")


def _print_summary(summary: ImportSummary, verbose: bool) -> None:
    for line in summary.header():
        print(line)
    shown = summary.notes if verbose else summary.notes[:25]
    for line in shown:
        print(line)
    if len(summary.notes) > len(shown):
        print(f"  … {len(summary.notes) - len(shown)} more (use --verbose)")


def cmd_import(args) -> None:
    cfg, reps, progress, _ = _context(args)
    files = sorted(p for p in cfg.inbox.iterdir() if p.suffix.lower() in (".pgn", ".txt"))
    if not files:
        print(f"No .pgn/.txt files in {cfg.inbox}. Paste or copy your games there, then run import again.")
        return
    summary = ImportSummary()
    for f in files:
        import_text(
            f.read_text(errors="replace"),
            reps=reps,
            progress=progress,
            player="me",
            names=cfg.my_names(),
            skip_bullet=cfg.skip_bullet,
            summary=summary,
        )
        cfg.done.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), cfg.done / f.name)
    _print_summary(summary, args.verbose)
    print(f"Moved {len(files)} file(s) to {cfg.done}.")


def cmd_fetch(args) -> None:
    cfg, reps, progress, _ = _context(args)
    summary = ImportSummary()
    try:
        if args.player:
            p = cfg.player(args.player)
            if p is None:
                sys.exit(f"'{args.player}' isn't in the [[players]] watch list in config.toml.")
            months = args.months or cfg.player_months
            print(f"Fetching {months} month(s) of {p.username} ({p.site})…")
            if p.site == "chesscom":
                texts = chesscom_games(p.username, months, me=cfg.chesscom_user)
            else:
                texts = [lichess_games(p.username, months, cfg.lichess_token)]
            out = cfg.creators_dir / p.alias
            out.mkdir(parents=True, exist_ok=True)
            (out / "latest-fetch.pgn").write_text("\n\n".join(texts))
            for text in texts:
                import_text(
                    text,
                    reps=reps,
                    progress=progress,
                    player=p.alias,
                    names={p.username},
                    skip_bullet=cfg.skip_bullet,
                    summary=summary,
                )
            _print_summary(summary, args.verbose)
            print(f"\nNext: chesstrain report {p.alias}   or   chesstrain guess {p.alias}")
        else:
            if not (cfg.chesscom_user or cfg.lichess_user):
                sys.exit("Set [me] chesscom and/or lichess usernames in config.toml first.")
            months = args.months or cfg.my_months
            texts = []
            if cfg.chesscom_user:
                print(f"Fetching {months} month(s) of {cfg.chesscom_user} from chess.com…")
                texts += chesscom_games(cfg.chesscom_user, months, me=cfg.chesscom_user)
            if cfg.lichess_user:
                print(f"Fetching {months} month(s) of {cfg.lichess_user} from Lichess…")
                texts.append(lichess_games(cfg.lichess_user, months, cfg.lichess_token))
            for text in texts:
                import_text(
                    text,
                    reps=reps,
                    progress=progress,
                    player="me",
                    names=cfg.my_names(),
                    skip_bullet=cfg.skip_bullet,
                    summary=summary,
                )
            _print_summary(summary, args.verbose)
    except FetchError as e:
        sys.exit(str(e))


def _player_alias(cfg: Config, name: str) -> str:
    p = cfg.player(name)
    if p is None:
        sys.exit(f"'{name}' isn't in the [[players]] watch list in config.toml.")
    return p.alias


def cmd_report(args) -> None:
    cfg, reps, progress, _ = _context(args)
    alias = _player_alias(cfg, args.player)
    system = _system(reps, args.system).system.key if args.system else None
    for line in cr.report_lines(cr.divergences(reps, progress, alias), reps, alias, system):
        print(line)


def cmd_guess(args) -> None:
    cfg, reps, progress, display = _context(args)
    alias = _player_alias(cfg, args.player)
    system = _system(reps, args.system).system.key if args.system else None
    source = _source(display)
    rng = random.Random()
    for _ in range(args.games):
        row = cr.pick_game(progress, alias, system, rng)
        if row is None:
            sys.exit(f"No stored {alias} games in your systems. Run: chesstrain fetch --player {alias}")
        rep = reps[row["system"]]
        colour = chess.WHITE if row["colour"] == "white" else chess.BLACK
        good, total, quit_ = cr.guess_game(rep, cr._game(row["pgn"]), colour, alias, source, display, args.moves)
        if total:
            progress.add_guess(alias, row["id"], row["system"], good, total)
        if quit_:
            break


def cmd_adopt(args) -> None:
    cfg, reps, progress, _ = _context(args)
    alias = _player_alias(cfg, args.player)
    divs = cr.divergences(reps, progress, alias)
    if not 1 <= args.number <= len(divs):
        sys.exit(f"No suggestion [{args.number}]. Run: chesstrain report {alias}")
    d = divs[args.number - 1]
    rep = reps[d.system]
    line = cr.adopt_line(d, args.plies)
    print(f"Add to {rep.system.name}: {chess.Board().variation_san(line)}")
    print(f"(as an alternative to {d.board.san(d.expected)}; both will be accepted, your current move stays main)")
    if not args.yes and input("Add this line? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Not changed.")
        return
    target = rep.system.folder / "repertoire.pgn"
    cr.add_line_to_pgn(target, line, f"adopted from {alias}")
    print(f"Updated {target}.")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="chesstrain", description="Opening repertoire trainer")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("systems", help="list systems and progress")
    p.set_defaults(func=cmd_systems)

    p = sub.add_parser("drill", help="play through lines of one system (or 'all' for a random mix)")
    p.add_argument("system", help="pirc, jobava, caro, colle, or all")
    p.add_argument(
        "--from", dest="start", type=int, default=0, metavar="N", help="auto-play your first N moves before asking"
    )
    p.add_argument("--every", type=int, default=1, metavar="K", help="ask every Kth of your moves")
    p.add_argument("--lines", type=int, default=5, help="lines to play (default 5)")
    p.add_argument("--new", type=int, help="override new positions per day")
    p.add_argument("--ascii", action="store_true", help="letters instead of chess pieces")
    p.add_argument("--large", action="store_true", help="big squares (7x3) instead of compact")
    p.add_argument("--window", action="store_true", help="show the board in a separate window")
    p.add_argument("--no-window", action="store_true", help="terminal board even if config enables the window")
    p.set_defaults(func=cmd_drill)

    p = sub.add_parser("review", help="review due positions across systems")
    p.add_argument("system", nargs="?")
    p.add_argument("--limit", type=int)
    p.add_argument("--ascii", action="store_true", help="letters instead of chess pieces")
    p.add_argument("--large", action="store_true", help="big squares (7x3) instead of compact")
    p.add_argument("--window", action="store_true", help="show the board in a separate window")
    p.add_argument("--no-window", action="store_true", help="terminal board even if config enables the window")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("stats", help="progress, accuracy, weak spots, gaps")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("import", help="import PGNs dropped into games/inbox/")
    p.add_argument("--verbose", "-v", action="store_true")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("fetch", help="download your chess.com games (or a watched player's)")
    p.add_argument("--player", help="alias from the watch list in config.toml")
    p.add_argument("--months", type=int)
    p.add_argument("--verbose", "-v", action="store_true")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("report", help="how a watched player plays your systems")
    p.add_argument("player")
    p.add_argument("--system")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("guess", help="guess a watched player's moves in their games")
    p.add_argument("player")
    p.add_argument("--system")
    p.add_argument("--moves", type=int, default=20, help="stop after this move number")
    p.add_argument("--games", type=int, default=1)
    p.add_argument("--ascii", action="store_true", help="letters instead of chess pieces")
    p.add_argument("--large", action="store_true", help="big squares (7x3) instead of compact")
    p.add_argument("--window", action="store_true", help="show the board in a separate window")
    p.add_argument("--no-window", action="store_true", help="terminal board even if config enables the window")
    p.set_defaults(func=cmd_guess)

    p = sub.add_parser("adopt", help="copy a suggested line from `report` into your repertoire")
    p.add_argument("player")
    p.add_argument("number", type=int)
    p.add_argument("--plies", type=int, default=8, help="extra half-moves after the new move")
    p.add_argument("--yes", "-y", action="store_true")
    p.set_defaults(func=cmd_adopt)
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
