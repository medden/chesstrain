# chesstrain

- Checks: `.venv/bin/pre-commit run --all-files` (ruff check, ruff format, pytest), the same as CI.
- Manual CLI runs: set `CHESSTRAIN_HOME` to a scratch copy (`config.toml`, `config.local.toml`, `repertoires/`); otherwise runs write to the user's real `data/progress.sqlite`.
- Repertoire PGNs are generated from `scripts/seeds/*.txt` by `scripts/build_repertoire.py`. `.built-hash` marks a PGN as untouched; the builder skips PGNs edited by hand or via `chesstrain adopt` unless `--force`. Edit the PGN or the seed, knowing which one wins.
- Personal settings (usernames, `[[players]]` watch list, Lichess token) live in git-ignored `config.local.toml`, merged over `config.toml`.
- Board rendering targets kitty inside herdr: keep every glyph single-width and avoid bold on piece glyphs. The `--window` board (`window.py`) is the place for visual polish.
