"""Turn dictated moves ("Knight to F3.", "e takes d5", "castles kingside") into moves.

Speech-to-text output is messy: capitals, full stops, "night" for knight, "for"
for 4. Rather than rebuilding SAN, pull out the piece, squares and capture
words, then pick the one legal move that fits. Ambiguous input returns None.
"""

from __future__ import annotations

import re

import chess

PIECES = {
    "king": chess.KING,
    "kings": chess.KING,
    "queen": chess.QUEEN,
    "queens": chess.QUEEN,
    "rook": chess.ROOK,
    "rooks": chess.ROOK,
    "rock": chess.ROOK,
    "brook": chess.ROOK,
    "ruk": chess.ROOK,
    "bishop": chess.BISHOP,
    "bishops": chess.BISHOP,
    "knight": chess.KNIGHT,
    "knights": chess.KNIGHT,
    "night": chess.KNIGHT,
    "nights": chess.KNIGHT,
    "nite": chess.KNIGHT,
    "horse": chess.KNIGHT,
    "pawn": chess.PAWN,
    "pawns": chess.PAWN,
    "pond": chess.PAWN,
    "porn": chess.PAWN,
    "prawn": chess.PAWN,
}
FILES = {
    **{f: f for f in "abcdefgh"},
    "alpha": "a",
    "bravo": "b",
    "charlie": "c",
    "delta": "d",
    "echo": "e",
    "foxtrot": "f",
    "golf": "g",
    "hotel": "h",
    "bee": "b",
    "be": "b",
    "see": "c",
    "sea": "c",
    "si": "c",
    "dee": "d",
    "eff": "f",
    "ef": "f",
    "gee": "g",
    "ji": "g",
    "aitch": "h",
    "age": "h",
    "eh": "a",
    "ee": "e",
}
RANKS = {
    **{str(n): str(n) for n in range(1, 9)},
    "one": "1",
    "won": "1",
    "two": "2",
    "too": "2",
    "to": "2",
    "three": "3",
    "tree": "3",
    "four": "4",
    "for": "4",
    "fore": "4",
    "five": "5",
    "six": "6",
    "sicks": "6",
    "seven": "7",
    "eight": "8",
    "ate": "8",
}
CAPTURE = {"takes", "take", "x", "captures", "capture", "by", "cross"}
PROMOTE = {"promotes", "promote", "promoting", "equals", "becomes"}
KINGSIDE = {"kingside", "short"}
QUEENSIDE = {"queenside", "long"}
FILLER = {
    "to",
    "too",
    "the",
    "a",
    "on",
    "moves",
    "move",
    "goes",
    "go",
    "plays",
    "play",
    "and",
    "check",
    "checkmate",
    "mate",
    "please",
    "um",
    "uh",
    "er",
    "okay",
    "ok",
    "side",
    "square",
}


def tokens(text: str) -> list[str]:
    text = text.lower().replace("-", " ")
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    out = []
    for tok in text.split():
        m = re.fullmatch(r"([a-h])([1-8])", tok)
        if m:
            out += [m.group(1), m.group(2)]
        else:
            out.append(tok)
    return out


def _castle(board: chess.Board, words: list[str]) -> chess.Move | None:
    # Left/right as seen from the mover's seat: White's left is the queenside,
    # Black's left (board turned round) is the kingside.
    if "left" in words or "right" in words:
        left_is_long = board.turn == chess.WHITE
        words = words + (["long"] if ("left" in words) == left_is_long else ["short"])
    if any(w in QUEENSIDE for w in words) or ("queen" in words and "side" in words):
        side = "O-O-O"
    elif any(w in KINGSIDE for w in words) or ("king" in words and "side" in words):
        side = "O-O"
    else:
        legal = [s for s in ("O-O", "O-O-O") if _try_san(board, s)]
        side = legal[0] if len(legal) == 1 else "O-O"
    return _try_san(board, side)


def _try_san(board: chess.Board, san: str) -> chess.Move | None:
    try:
        return board.parse_san(san)
    except ValueError:
        return None


def spoken_to_move(board: chess.Board, text: str) -> chess.Move | None:
    words = tokens(text)
    if not words:
        return None
    if any(w.startswith("castle") for w in words):
        return _castle(board, words)

    piece = None
    promo = None
    capture = False
    squares: list[int] = []
    from_files: list[str] = []
    expect_promo = False
    i = 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else None
        after = words[i + 2] if i + 2 < len(words) else None
        # "b to c3": "to" before another square is filler, not the rank 2.
        filler_to = nxt in ("to", "too") and after in FILES
        if w in FILES and nxt in RANKS and not filler_to:
            squares.append(chess.parse_square(FILES[w] + RANKS[nxt]))
            i += 2
            continue
        if w in PIECES:
            # A piece named after the destination ("e8 queen") is a promotion.
            if expect_promo or squares:
                promo = PIECES[w]
            elif piece is None:
                piece = PIECES[w]
        elif w in PROMOTE:
            expect_promo = True
        elif w in CAPTURE:
            capture = True
        elif w in FILES and len(w) == 1 and w != "a":
            from_files.append(w)
        elif w == "a" and nxt in CAPTURE:
            from_files.append("a")
        i += 1

    if not squares:
        return None
    dest = squares[-1]
    origin = squares[0] if len(squares) >= 2 else None

    candidates = [m for m in board.legal_moves if m.to_square == dest]
    if origin is not None:
        candidates = [m for m in candidates if m.from_square == origin]
    if piece is not None:
        candidates = [m for m in candidates if board.piece_type_at(m.from_square) == piece]
    if from_files:
        hinted = [m for m in candidates if chess.FILE_NAMES[chess.square_file(m.from_square)] in from_files]
        candidates = hinted or candidates
    if capture:
        caps = [m for m in candidates if board.is_capture(m)]
        candidates = caps or candidates
    if any(m.promotion for m in candidates):
        want = promo or chess.QUEEN
        candidates = [m for m in candidates if m.promotion == want]
    if piece is None and origin is None and len(candidates) > 1:
        pawns = [m for m in candidates if board.piece_type_at(m.from_square) == chess.PAWN]
        candidates = pawns or candidates
    return candidates[0] if len(candidates) == 1 else None
