from pathlib import Path

import chess

from chesstrain import creators as cr
from chesstrain.db import Progress
from chesstrain.games import ImportSummary, import_text
from chesstrain.repertoire import Repertoire, System, read_games


def _rep(key, colour, pgn):
    return Repertoire(System(key, key, colour, Path(".")), read_games(pgn))


def _game(moves, white, black, result="1-0"):
    return f'[White "{white}"]\n[Black "{black}"]\n[TimeControl "600+0"]\n[Result "{result}"]\n\n{moves} {result}\n\n'


def _setup():
    reps = {
        "caro": _rep("caro", chess.BLACK, "1. e4 c6 2. d4 d5 3. e5 Bf5 *"),
        "jobava": _rep("jobava", chess.WHITE, "1. d4 d5 2. Nc3 Nf6 3. Bf4 e6 4. e3 *"),
    }
    p = Progress(":memory:")
    s = ImportSummary()
    text = (
        # Two Caro divergences (3...c5) and one Jobava divergence (4.Nb5): Caro sorts first.
        _game("1. e4 c6 2. d4 d5 3. e5 c5", "opp1", "Creator", "0-1")
        + _game("1. e4 c6 2. d4 d5 3. e5 c5", "opp2", "Creator", "0-1")
        + _game("1. d4 d5 2. Nc3 Nf6 3. Bf4 e6 4. Nb5", "Creator", "opp3")
    )
    import_text(text, reps=reps, progress=p, player="cr", names={"creator"}, skip_bullet=True, summary=s)
    return reps, p


def test_filtered_report_keeps_global_numbers_for_adopt():
    reps, p = _setup()
    divs = cr.divergences(reps, p, "cr")
    assert [d.system for d in divs] == ["caro", "jobava"]
    lines = cr.report_lines(divs, reps, "cr", system="jobava")
    numbered = [line for line in lines if line.strip().startswith("[")]
    # The Jobava row keeps number [2], so `adopt cr 2` picks the line the user saw.
    assert len(numbered) == 1 and numbered[0].strip().startswith("[2]") and "Nb5" in numbered[0]
    assert divs[2 - 1].system == "jobava"


def test_divergence_counts_and_score():
    reps, p = _setup()
    caro = cr.divergences(reps, p, "cr")[0]
    assert caro.count == 2 and caro.score == 2.0
    assert caro.board.san(caro.played) == "c5"


def test_adopt_adds_alternative(tmp_path):
    reps, p = _setup()
    d = cr.divergences(reps, p, "cr")[1]
    pgn = tmp_path / "repertoire.pgn"
    pgn.write_text("1. d4 d5 2. Nc3 Nf6 3. Bf4 e6 4. e3 *\n")
    cr.add_line_to_pgn(pgn, cr.adopt_line(d, extra_plies=0), "adopted from cr")
    rep = _rep("jobava", chess.WHITE, pgn.read_text())
    moves = [d.board.san(m) for m in rep.moves_at(d.board)]
    assert moves == ["e3", "Nb5"]  # existing main move stays first
