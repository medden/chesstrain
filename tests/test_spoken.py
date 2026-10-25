import chess
import pytest

from chesstrain.moves import command_of, parse_move


def board(*sans, fen=None):
    b = chess.Board(fen) if fen else chess.Board()
    for s in sans:
        b.push_san(s)
    return b


@pytest.mark.parametrize("text,san", [
    ("Knight to F3.", "Nf3"),
    ("knight f3", "Nf3"),
    ("Night to F 3", "Nf3"),
    ("Knight F-3.", "Nf3"),
    ("E4.", "e4"),
    ("e four", "e4"),
    ("pawn to e4", "e4"),
    ("D4", "d4"),
    ("Echo four", "e4"),
    ("g1 f3", "Nf3"),
])
def test_start_position(text, san):
    b = chess.Board()
    assert parse_move(b, text) == b.parse_san(san), text


def test_captures_and_from_file():
    b = board("e4", "d5")
    assert parse_move(b, "E takes D5.") == b.parse_san("exd5")
    assert parse_move(b, "pawn takes d5") == b.parse_san("exd5")
    b = board("e4", "d6", "d4", "Nf6", "Nc3", "g6", "f4", "Bg7", "Nf3", "O-O", "Bd3", "Na6", "O-O", "c5", "d5")
    assert parse_move(b, "Knight to C7") == b.parse_san("Nc7")
    assert parse_move(b, "Bishop to G4.") == b.parse_san("Bg4")


def test_ambiguous_knights_need_file():
    b = chess.Board("4k3/8/8/8/8/8/8/1N1NK3 w - - 0 1")  # knights b1, d1 -> c3
    assert parse_move(b, "knight to c3") is None
    assert parse_move(b, "knight b to c3") == b.parse_san("Nbc3")
    assert parse_move(b, "knight d c3") == b.parse_san("Ndc3")


def test_castling():
    b = chess.Board("r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1")
    assert parse_move(b, "Castles kingside.") == b.parse_san("O-O")
    assert parse_move(b, "castle short") == b.parse_san("O-O")
    assert parse_move(b, "Castles queen side") == b.parse_san("O-O-O")
    assert parse_move(b, "castle long") == b.parse_san("O-O-O")
    b2 = chess.Board("r3k2r/pppppppp/8/8/8/8/PPPPPPPP/4K2R w Kkq - 0 1")
    assert parse_move(b2, "Castles.") == b2.parse_san("O-O")


def test_promotion():
    b = chess.Board("8/4P3/8/8/8/8/k7/4K3 w - - 0 1")
    assert parse_move(b, "e8 queen") == chess.Move.from_uci("e7e8q")
    assert parse_move(b, "e8 promotes to knight") == chess.Move.from_uci("e7e8n")
    assert parse_move(b, "e8") == chess.Move.from_uci("e7e8q")


def test_rank_homophones():
    b = board("e4", "e5")
    assert parse_move(b, "Knight to F for.") is None  # f4 is not a knight square
    assert parse_move(b, "pawn to F for") == b.parse_san("f4")
    assert parse_move(b, "Bishop to C for.") == b.parse_san("Bc4")


def test_garbage():
    assert parse_move(chess.Board(), "hello there") is None
    assert parse_move(chess.Board(), "queen to h5") is None


@pytest.mark.parametrize("text,cmd", [("Hint.", "hint"), ("Skip!", "skip"), ("Quit.", "quit"), ("stop", "quit"), ("e4", None)])
def test_commands(text, cmd):
    assert command_of(text) == cmd


def test_help_in_quiz_and_castling_side():
    from chesstrain.moves import ScriptedMoveSource, notation_help
    from chesstrain.session import Display, Outcome, quiz
    assert "castle right: O-O " in notation_help(chess.WHITE)
    assert "castle left: O-O " in notation_help(chess.BLACK)
    d = Display(colour=False, out=[])
    b = chess.Board()
    b.push_san("e4")
    out = quiz(b, [b.parse_san("d6")], ScriptedMoveSource(["?", "Help.", "d6"]), d, chess.BLACK)
    assert out[0] == Outcome.GOOD  # asking for help isn't penalised
    assert sum("Move notation" in line for line in d.out) == 2


def test_castle_left_right_depends_on_colour():
    w = chess.Board("r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1")
    b = chess.Board("r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R b KQkq - 0 1")
    assert parse_move(w, "castle left") == w.parse_san("O-O-O")
    assert parse_move(w, "Castle right.") == w.parse_san("O-O")
    assert parse_move(b, "castle left") == b.parse_san("O-O")
    assert parse_move(b, "right") == b.parse_san("O-O-O")
