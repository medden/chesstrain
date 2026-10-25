# chesstrain

Personal opening trainer: drill your repertoire with spaced repetition (FSRS),
learn from your own chess.com games, and study how creators play your systems.

Systems (one folder each under `repertoires/`): Pirc (black), Jobava London (white),
Caro-Kann (black), Colle-Zukertort (white).

## Setup

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp config.local.example.toml config.local.toml   # then add your usernames
ln -s "$PWD/.venv/bin/chesstrain" ~/.local/bin/chesstrain   # optional: put it on PATH
```

## Daily use

```sh
chesstrain review                          # everything due, all systems
chesstrain drill pirc                      # play 5 lines, quiz every move of yours
chesstrain drill jobava --from 3 --every 2 # auto-play your first 3 moves, then quiz every 2nd
chesstrain drill all                       # random system each line
chesstrain stats                           # learned / due / accuracy / weak spots / gaps
```

The board is compact by default (zoom the terminal to size it); `--large` gives big
squares, `--ascii` uses letters. Defaults live under `[display]` in `config.toml`.

While answering: type or say (Voxtype push-to-talk) `Nf3`, "knight to f3", `nf3`, `O-O`, `exd5` or `g1f3`; `hint` (shows which piece,
counts as Hard), `skip`, `board`, `quit`. A wrong move shows the answer and you type it
to lock it in; the position comes back soon.

## Your games

- Drop or paste PGNs into `games/inbox/` and run `chesstrain import` (files move to `games/done/`).
- Or `chesstrain fetch [--months N]` to pull your chess.com and Lichess games
  (`[me] chesscom` / `lichess` in `config.local.toml`).

Each game is matched to a system. If **you** left the repertoire, that position goes
straight into your next review. If the **opponent** played something unprepared, it's
listed under "gaps" in `chesstrain stats`. Bullet games are skipped.

## Creators

Add creators to the `[[players]]` watch list in `config.local.toml` (alias, site, username).

```sh
chesstrain fetch --player <alias> # last 3 months by default
chesstrain report <alias>          # where they play differently from your repertoire
chesstrain guess <alias>           # guess-the-move through one of their games
chesstrain adopt <alias> 3         # copy suggestion [3] into your repertoire (asks first)
```

## Editing the repertoire

The PGNs in `repertoires/<system>/` are the answer key — edit them in any PGN editor
or by hand. Every `.pgn` in a folder is merged; the first move in a position is the
"main" answer, and other moves for your side are also accepted.

The baseline PGNs were generated from `scripts/seeds/*.txt` (one line per row):

```sh
.venv/bin/python scripts/build_repertoire.py            # rebuild from seeds
.venv/bin/python scripts/build_repertoire.py --explorer # + coverage check vs Lichess explorer
```

The builder won't overwrite a PGN you've edited by hand (or adopted into) unless you
pass `--force`. `--explorer` needs a free Lichess token (no scopes) from
https://lichess.org/account/oauth/token, in `LICHESS_TOKEN` or `config.local.toml`.

Progress lives in `data/progress.sqlite` (git-ignored).
