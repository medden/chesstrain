"""Project paths and configuration loading."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def project_root() -> Path:
    env = os.environ.get("CHESSTRAIN_HOME")
    if env:
        return Path(env).expanduser().resolve()
    # src/chesstrain/config.py -> project root (editable install)
    return Path(__file__).resolve().parents[2]


@dataclass
class Player:
    alias: str
    site: str  # "chesscom" | "lichess"
    username: str


@dataclass
class Config:
    root: Path
    chesscom_user: str | None = None
    lichess_user: str | None = None
    lichess_token: str | None = None
    new_per_day: int = 10
    skip_bullet: bool = True
    my_months: int = 1
    player_months: int = 3
    players: list[Player] = field(default_factory=list)
    pieces: str = "icons"
    small_board: bool = True
    window: bool = False
    window_port: int = 8765

    @property
    def repertoire_dir(self) -> Path:
        return self.root / "repertoires"

    @property
    def db_path(self) -> Path:
        return self.root / "data" / "progress.sqlite"

    @property
    def inbox(self) -> Path:
        return self.root / "games" / "inbox"

    @property
    def done(self) -> Path:
        return self.root / "games" / "done"

    @property
    def creators_dir(self) -> Path:
        return self.root / "games" / "creators"

    def my_names(self) -> set[str]:
        return {n.lower() for n in (self.chesscom_user, self.lichess_user) if n}

    def player(self, alias: str) -> Player | None:
        alias = alias.lower()
        for p in self.players:
            if p.alias.lower() == alias or p.username.lower() == alias:
                return p
        return None


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("rb") as f:
        return tomllib.load(f)


def load_config(root: Path | None = None) -> Config:
    root = root or project_root()
    data = _read(root / "config.toml")
    local = _read(root / "config.local.toml")
    me = data.get("me", {})
    training = data.get("training", {})
    fetch = data.get("fetch", {})
    display = data.get("display", {})
    token = os.environ.get("LICHESS_TOKEN") or local.get("lichess", {}).get("token")
    return Config(
        root=root,
        chesscom_user=me.get("chesscom"),
        lichess_user=me.get("lichess"),
        lichess_token=token,
        new_per_day=training.get("new_per_day", 10),
        skip_bullet=training.get("skip_bullet", True),
        my_months=fetch.get("my_months", 1),
        player_months=fetch.get("player_months", 3),
        pieces=display.get("pieces", "icons"),
        small_board=display.get("small", True),
        window=display.get("window", False),
        window_port=display.get("window_port", 8765),
        players=[Player(p["alias"], p["site"], p["username"]) for p in data.get("players", [])],
    )
