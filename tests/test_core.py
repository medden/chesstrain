import random
from datetime import timedelta
from pathlib import Path

import chess
import chess.pgn
import fsrs
import pytest

from chesstrain.analysis import detect, is_bullet
from chesstrain.config import load_config
from chesstrain.db import Progress, now_utc
from chesstrain.games import ImportSummary, import_text
from chesstrain.moves import ScriptedMoveSource, parse_move
from chesstrain.repertoire import Repertoire, System, load_all, position_key, read_games
from chesstrain.session import Display, Outcome, Trainer, quiz, review

ROOT = Path(__file__).resolve().parents[1]

PIRC_PGN = """
[Event "test"]
1. e4 d6 2. d4 (2. Nf3 Nf6 3. Nc3 g6 4. d4 Bg7) 2... Nf6 3. Nc3 g6 4. f4 (4. Nf3 Bg7) 4... Bg7 *
"""


def make_rep(pgn=PIRC_PGN, colour=chess.BLACK):
    return Repertoire(System("pirc", "Pirc", colour, Path(".")), read_games(pgn))


def play(*sans):
    b = chess.Board()
    for s in sans:
        b.push_san(s)
    return b


# -- repertoire ---------------------------------------------------------------


def test_card_positions_and_transposition():
    rep = make_rep()
    # Black to move after: e4, e4 d6 d4, e4 d6 d4 Nf6 Nc3, ...g6 f4, ...g6 Nf3, e4 d6 Nf3, e4 d6 Nf3 Nf6 Nc3
    # 2.Nf3 ... 4.d4 transposes into 1.e4 d6 2.d4 Nf6 3.Nc3 g6 4.Nf3.
    a = position_key(play("e4", "d6", "d4", "Nf6", "Nc3", "g6", "Nf3"))
    b = position_key(play("e4", "d6", "Nf3", "Nf6", "Nc3", "g6", "d4"))
    assert a == b
    assert a in rep.card_keys()
    assert len(rep.card_keys()) == 7


def test_path_is_shortest_and_replays():
    rep = make_rep()
    key = position_key(play("e4", "d6", "d4", "Nf6", "Nc3", "g6", "f4"))
    board = rep.board_for(key)
    assert position_key(board) == key
    assert rep.moves_at(board) == [chess.Move.from_uci("f8g7")]


def test_mainline_first_for_opponent_moves():
    rep = make_rep()
    assert [m.uci() for m in rep.moves_at(play("e4", "d6"))] == ["d2d4", "g1f3"]


def test_shipped_repertoires_load():
    reps = load_all(load_config(ROOT).repertoire_dir)
    assert set(reps) == {"pirc", "jobava-london", "caro-kann", "colle"}
    for rep in reps.values():
        assert len(rep.card_keys()) > 40
        assert rep.alternatives() == []


# -- move parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text,uci",
    [
        ("Nf3", "g1f3"),
        ("nf3", "g1f3"),
        ("g1f3", "g1f3"),
        ("e4", "e2e4"),
        ("E4", "e2e4"),
    ],
)
def test_parse_move_start(text, uci):
    assert parse_move(chess.Board(), text) == chess.Move.from_uci(uci)


def test_parse_castling_and_pawn_vs_bishop():
    b = chess.Board("r3k2r/8/8/8/8/2n5/1P6/R3K2R w KQkq - 0 1")
    assert parse_move(b, "0-0") == chess.Move.from_uci("e1g1")
    assert parse_move(b, "o-o-o") is None  # knight on c3 covers d1
    assert parse_move(b, "bxc3") == chess.Move.from_uci("b2c3")
    assert parse_move(b, "zz") is None
    b2 = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    assert parse_move(b2, "O-O-O") == chess.Move.from_uci("e1c1")
    b3 = chess.Board("4k3/8/8/8/8/2p5/1P6/2B1K3 w - - 0 1")
    assert parse_move(b3, "bxc3") == chess.Move.from_uci("b2c3")
    assert parse_move(b3, "Bxc3") is None


# -- quiz / FSRS ----------------------------------------------------------------


def _quiet():
    return Display(colour=False, out=[])


def test_quiz_outcomes():
    b = play("e4")
    accepted = [chess.Move.from_uci("d7d6")]
    assert quiz(b, accepted, ScriptedMoveSource(["d6"]), _quiet(), chess.BLACK)[0] == Outcome.GOOD
    assert quiz(b, accepted, ScriptedMoveSource(["hint", "d6"]), _quiet(), chess.BLACK)[0] == Outcome.HARD
    out = quiz(b, accepted, ScriptedMoveSource(["e5", "c6", "d6"]), _quiet(), chess.BLACK)
    assert out == (Outcome.AGAIN, accepted[0])
    # Unreadable input isn't penalised.
    assert quiz(b, accepted, ScriptedMoveSource(["xx", "d6"]), _quiet(), chess.BLACK)[0] == Outcome.GOOD


def test_wrong_answer_schedules_soon_and_counts_lapse():
    p = Progress(":memory:")
    now = now_utc()
    row = p.review("pirc", "k", fsrs.Rating.Again, "train", at=now)
    assert row.lapses == 1 and row.due - now <= timedelta(minutes=10)
    row = p.review("pirc", "k", fsrs.Rating.Good, "train", at=now + timedelta(minutes=2))
    row = p.review("pirc", "k", fsrs.Rating.Good, "train", at=now + timedelta(minutes=20))
    assert row.due - now >= timedelta(hours=12)


# -- drill ----------------------------------------------------------------------


def test_drill_from_and_every():
    rep = make_rep()
    p = Progress(":memory:")
    # --from 1 --every 2: skip my 1st move (d6), ask 2nd (Nf6), skip 3rd (g6), ask 4th (Bg7).
    src = ScriptedMoveSource(["Nf6", "Bg7"])
    t = Trainer(rep, p, src, _quiet(), rng=random.Random(1))
    assert t.drill_line(start_after=1, every=2) is True
    asked = sorted(len(rep.board_for(r.key).move_stack) for r in p.cards("pirc"))
    assert asked[0] == 3 and asked[1] in (7,)  # black's 2nd and 4th moves


def test_new_card_limit_ends_line():
    rep = make_rep()
    p = Progress(":memory:")
    t = Trainer(rep, p, ScriptedMoveSource(["d6", "Nf6"]), _quiet(), new_per_day=1)
    t.drill_line()
    assert len(p.cards("pirc")) == 1


def test_opponent_choice_prefers_due_branch():
    rep = make_rep()
    p = Progress(":memory:")
    now = now_utc()
    # Make every card in the 2.d4 branch already learned & not due; 2.Nf3 branch new.
    board = play("e4", "d6", "d4")
    for k in rep.subtree_cards(position_key(board)):
        for _ in range(3):
            p.review("pirc", k, fsrs.Rating.Good, "train", at=now)
        assert p.get("pirc", k).due > now
    t = Trainer(rep, p, ScriptedMoveSource([]), _quiet(), new_per_day=99, rng=random.Random(0))
    picks = [t.choose_opponent_move(play("e4", "d6")).uci() for _ in range(50)]
    assert picks.count("g1f3") > picks.count("d2d4")


def test_review_shows_due_then_stops():
    rep = make_rep()
    p = Progress(":memory:")
    key = position_key(play("e4"))
    p.review("pirc", key, fsrs.Rating.Good, "train", at=now_utc() - timedelta(days=60))
    disp = _quiet()
    tally = review({"pirc": rep}, p, ScriptedMoveSource(["d6"]), disp)
    assert tally.good == 1
    assert p.due() == []


# -- games ------------------------------------------------------------------------


def _game(moves, white="me_user", black="opp", tc="600+0"):
    return f'[White "{white}"]\n[Black "{black}"]\n[TimeControl "{tc}"]\n[Result "*"]\n\n{moves} *\n\n'


def test_detect_and_deviation_by_me_requeues_card():
    reps = {"pirc": make_rep()}
    p = Progress(":memory:")
    s = ImportSummary()
    text = _game("1. e4 d6 2. d4 Nf6 3. Nc3 e5", white="opp", black="me_user")
    import_text(text, reps=reps, progress=p, player="me", names={"me_user"}, skip_bullet=True, summary=s)
    assert s.counts["dev_me"] == 1
    card = p.cards("pirc")[0]
    assert card.lapses == 1
    assert [c.key for c in p.due("pirc")] == [card.key]
    assert position_key(reps["pirc"].board_for(card.key)) == position_key(play("e4", "d6", "d4", "Nf6", "Nc3"))


def test_gap_recorded_when_opponent_leaves_book():
    reps = {"pirc": make_rep()}
    p = Progress(":memory:")
    s = ImportSummary()
    text = _game("1. e4 d6 2. d4 Nf6 3. Bd3 e5", white="opp", black="me_user")
    import_text(text, reps=reps, progress=p, player="me", names={"me_user"}, skip_bullet=True, summary=s)
    gaps = p.gaps("pirc")
    assert len(gaps) == 1 and gaps[0]["move_san"] == "Bd3"
    # Re-import is deduplicated.
    import_text(text, reps=reps, progress=p, player="me", names={"me_user"}, skip_bullet=True, summary=s)
    assert s.counts["duplicate"] == 1 and p.gaps("pirc")[0]["count"] == 1


def test_unmatched_and_bullet_skipped():
    reps = {"pirc": make_rep()}
    p = Progress(":memory:")
    s = ImportSummary()
    text = _game("1. e4 c5 2. Nf3", white="opp", black="me_user") + _game(
        "1. e4 d6", tc="60+0", white="opp", black="me_user"
    )
    import_text(text, reps=reps, progress=p, player="me", names={"me_user"}, skip_bullet=True, summary=s)
    assert s.counts["unmatched"] == 1 and s.counts["bullet"] == 1


def test_is_bullet():
    assert is_bullet(chess.pgn.Game(headers={"TimeControl": "120+1"}))
    assert not is_bullet(chess.pgn.Game(headers={"TimeControl": "180+0"}))
    assert not is_bullet(chess.pgn.Game(headers={"TimeControl": "1/86400"}))


def test_shared_first_move_does_not_pick_a_system():
    jobava = Repertoire(System("jobava", "Jobava", chess.WHITE, Path(".")), read_games("1. d4 d5 2. Nc3 Nf6 3. Bf4 *"))
    colle = Repertoire(System("colle", "Colle", chess.WHITE, Path(".")), read_games("1. d4 d5 2. Nf3 Nf6 3. e3 *"))
    reps = {"jobava": jobava, "colle": colle}
    qg = read_games('[White "me"]\n\n1. d4 Nf6 2. c4 e6 *')[0]
    assert detect(reps, qg, chess.WHITE) is None
    jb = read_games('[White "me"]\n\n1. d4 d5 2. Nc3 c6 *')[0]
    m = detect(reps, jb, chess.WHITE)
    assert m.system == "jobava" and m.deviation_by == "opponent"


def test_shared_prefix_gap_recorded_for_each_system():
    jobava = Repertoire(System("jobava", "Jobava", chess.WHITE, Path(".")), read_games("1. d4 d5 2. Nc3 Nf6 3. Bf4 *"))
    colle = Repertoire(System("colle", "Colle", chess.WHITE, Path(".")), read_games("1. d4 d5 2. Nf3 Nf6 3. e3 *"))
    p = Progress(":memory:")
    s = ImportSummary()
    import_text(
        _game("1. d4 e5 2. dxe5"),
        reps={"jobava": jobava, "colle": colle},
        progress=p,
        player="me",
        names={"me_user"},
        skip_bullet=True,
        summary=s,
    )
    assert {g["system"] for g in p.gaps()} == {"jobava", "colle"}


def test_local_config_overrides(tmp_path):
    (tmp_path / "config.toml").write_text("[training]\nnew_per_day = 10\n[display]\nwindow = true\n")
    (tmp_path / "config.local.toml").write_text(
        '[me]\nchesscom = "u1"\n[training]\nnew_per_day = 5\n'
        '[[players]]\nalias = "x"\nsite = "lichess"\nusername = "X"\n'
    )
    cfg = load_config(tmp_path)
    assert cfg.chesscom_user == "u1" and cfg.new_per_day == 5 and cfg.window is True
    assert [p.username for p in cfg.players] == ["X"]
