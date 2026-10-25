"""Download games from chess.com and Lichess public APIs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

USER_AGENT = "chesstrain/0.1 (personal opening trainer)"


class FetchError(RuntimeError):
    pass


def _client(user_hint: str | None = None) -> httpx.Client:
    ua = USER_AGENT + (f"; chess.com user {user_hint}" if user_hint else "")
    return httpx.Client(headers={"User-Agent": ua}, timeout=30, follow_redirects=True)


def chesscom_games(username: str, months: int, me: str | None = None) -> list[str]:
    """PGNs of standard-chess games from the last `months` monthly archives."""
    with _client(me) as client:
        r = client.get(f"https://api.chess.com/pub/player/{username.lower()}/games/archives")
        if r.status_code == 404:
            raise FetchError(f"chess.com user '{username}' not found")
        r.raise_for_status()
        archives = r.json().get("archives", [])[-months:] if months > 0 else []
        pgns = []
        for url in archives:
            resp = client.get(url)
            resp.raise_for_status()
            for g in resp.json().get("games", []):
                if g.get("rules") == "chess" and g.get("pgn"):
                    pgns.append(g["pgn"])
        return pgns


def lichess_games(username: str, months: int, token: str | None = None, speeds: str = "blitz,rapid,classical") -> str:
    """One PGN text blob of the user's games since `months` ago."""
    since = datetime.now(timezone.utc) - timedelta(days=30 * months)
    headers = {"Accept": "application/x-chess-pgn"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {
        "since": int(since.timestamp() * 1000),
        "perfType": speeds,
        "moves": "true",
        "tags": "true",
        "clocks": "false",
        "evals": "false",
        "opening": "true",
    }
    with _client() as client:
        r = client.get(f"https://lichess.org/api/games/user/{username}", params=params, headers=headers, timeout=120)
        if r.status_code == 404:
            raise FetchError(f"Lichess user '{username}' not found")
        r.raise_for_status()
        return r.text
