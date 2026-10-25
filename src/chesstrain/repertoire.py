"""Repertoire trees loaded from PGN.

Every PGN file in a system folder is merged into one position graph keyed by
EPD, so transpositions between lines share the same node (and the same card).
"""

from __future__ import annotations

import io
import tomllib
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import chess
import chess.pgn


def position_key(board: chess.Board) -> str:
    """Position identity without move counters (so transpositions match)."""
    return board.epd()


@dataclass
class Node:
    key: str
    turn: chess.Color
    moves: list[chess.Move] = field(default_factory=list)  # mainline first
    path: list[chess.Move] = field(default_factory=list)  # shortest route from start


@dataclass
class System:
    key: str  # folder name, e.g. "pirc"
    name: str
    colour: chess.Color
    folder: Path

    @property
    def colour_name(self) -> str:
        return "white" if self.colour == chess.WHITE else "black"


class Repertoire:
    def __init__(self, system: System, games: list[chess.pgn.Game]):
        self.system = system
        self.nodes: dict[str, Node] = {}
        for game in games:
            self._add_tree(game)
        self._compute_paths()
        self._subtree_cache: dict[str, frozenset[str]] = {}

    # -- construction -------------------------------------------------------

    def _node(self, board: chess.Board) -> Node:
        key = position_key(board)
        node = self.nodes.get(key)
        if node is None:
            node = self.nodes[key] = Node(key, board.turn)
        return node

    def _add_tree(self, game: chess.pgn.Game) -> None:
        stack = [(game, game.board())]
        while stack:
            pgn_node, board = stack.pop()
            node = self._node(board)
            for var in pgn_node.variations:
                if var.move not in node.moves:
                    node.moves.append(var.move)
                child = board.copy(stack=False)
                child.push(var.move)
                stack.append((var, child))

    def _compute_paths(self) -> None:
        start = chess.Board()
        root = self.nodes.get(position_key(start))
        if root is None:
            return
        seen = {root.key}
        queue = deque([(start, [])])
        while queue:
            board, path = queue.popleft()
            node = self.nodes[position_key(board)]
            node.path = path
            for move in node.moves:
                child = board.copy(stack=False)
                child.push(move)
                key = position_key(child)
                if key in self.nodes and key not in seen:
                    seen.add(key)
                    queue.append((child, path + [move]))

    # -- queries ------------------------------------------------------------

    def node(self, board: chess.Board) -> Node | None:
        return self.nodes.get(position_key(board))

    def is_my_turn(self, board: chess.Board) -> bool:
        return board.turn == self.system.colour

    def moves_at(self, board: chess.Board) -> list[chess.Move]:
        node = self.node(board)
        return list(node.moves) if node else []

    def card_keys(self) -> list[str]:
        """Positions where it's my move and the repertoire has an answer."""
        return [
            n.key
            for n in self.nodes.values()
            if n.turn == self.system.colour and n.moves and (n.path or n.key == _START_KEY)
        ]

    def board_for(self, key: str) -> chess.Board | None:
        node = self.nodes.get(key)
        if node is None:
            return None
        board = chess.Board()
        for move in node.path:
            board.push(move)
        return board

    def subtree_cards(self, key: str) -> frozenset[str]:
        """All card keys reachable from (and including) a position."""
        if key in self._subtree_cache:
            return self._subtree_cache[key]
        result: set[str] = set()
        stack = [key]
        visited = set()
        while stack:
            k = stack.pop()
            if k in visited or k not in self.nodes:
                continue
            visited.add(k)
            node = self.nodes[k]
            if node.turn == self.system.colour and node.moves:
                result.add(k)
            board = chess.Board(k + " 0 1")
            for move in node.moves:
                board.push(move)
                stack.append(position_key(board))
                board.pop()
        frozen = frozenset(result)
        self._subtree_cache[key] = frozen
        return frozen

    def alternatives(self) -> list[tuple[str, list[chess.Move]]]:
        """Card positions with more than one prepared move (for validation)."""
        return [(k, self.nodes[k].moves) for k in self.card_keys() if len(self.nodes[k].moves) > 1]


_START_KEY = position_key(chess.Board())


def load_system(folder: Path) -> System:
    meta = {}
    meta_file = folder / "system.toml"
    if meta_file.exists():
        with meta_file.open("rb") as f:
            meta = tomllib.load(f)
    colour = meta.get("colour", "white").lower()
    return System(
        key=folder.name,
        name=meta.get("name", folder.name),
        colour=chess.WHITE if colour == "white" else chess.BLACK,
        folder=folder,
    )


def read_games(text: str) -> list[chess.pgn.Game]:
    stream = io.StringIO(text)
    games = []
    while (game := chess.pgn.read_game(stream)) is not None:
        games.append(game)
    return games


def load_repertoire(folder: Path) -> Repertoire:
    system = load_system(folder)
    games: list[chess.pgn.Game] = []
    for pgn in sorted(folder.glob("*.pgn")):
        games.extend(read_games(pgn.read_text()))
    return Repertoire(system, games)


def load_all(repertoire_dir: Path) -> dict[str, Repertoire]:
    reps = {}
    for folder in sorted(p for p in repertoire_dir.iterdir() if p.is_dir()):
        reps[folder.name] = load_repertoire(folder)
    return reps


def resolve_system(reps: dict[str, Repertoire], name: str) -> Repertoire | None:
    name = name.lower()
    if name in reps:
        return reps[name]
    matches = [r for k, r in reps.items() if k.startswith(name) or name in r.system.name.lower()]
    return matches[0] if len(matches) == 1 else None
