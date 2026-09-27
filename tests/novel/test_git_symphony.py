import os
import subprocess
import sys
import wave
from pathlib import Path

import mido
import pytest

from jiangkit.experimental import git_symphony as gs  # noqa: E402


def _git(repo, *args, env=None):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    authors = [("Alice", "a@x"), ("Bob", "b@x"), ("Carol", "c@x")]
    base = 1_700_000_000
    for i in range(9):
        name, mail = authors[i % 3]
        f = r / f"f{i % 4}.txt"
        n = 5 * (i + 1) if i != 6 else 800  # commit 6 is a "big refactor"
        f.write_text("\n".join(f"line {j} {i}" for j in range(n)) + "\n")
        env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=mail,
                   GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=mail,
                   GIT_AUTHOR_DATE=f"{base + i * 3600 * (i + 1)} +0000",
                   GIT_COMMITTER_DATE=f"{base + i * 3600 * (i + 1)} +0000")
        _git(r, "add", "-A", env=env)
        _git(r, "commit", "-q", "-m", f"c{i}", env=env)
    return r


def test_history_parsing(repo):
    commits = gs.read_history(str(repo))
    assert len(commits) == 9
    assert commits[0].author == "Alice"
    assert commits[6].added >= 800


def test_compose_mapping(repo):
    commits = gs.read_history(str(repo))
    score = gs.compose(commits)
    assert score.authors == ["Alice", "Bob", "Carol"]
    chords = [e for e in score.events if e.is_chord]
    assert len(chords) >= 1 and len(chords[0].pitches) >= 3
    # rests: start times strictly increase and gaps grow beyond one beat
    starts = [e.start for e in score.events]
    assert all(b > a for a, b in zip(starts, starts[1:]))
    assert max(b - a for a, b in zip(starts, starts[1:])) > 0.25


def test_pitch_monotonic():
    assert gs.scale_note(60, 1000) > gs.scale_note(60, 10) > gs.scale_note(60, 0) == 60


def test_cli_outputs(repo, tmp_path, capsys):
    mid, wav = tmp_path / "s.mid", tmp_path / "s.wav"
    assert gs.main(["--repo", str(repo), "--midi", str(mid), "--wav", str(wav)]) == 0
    m = mido.MidiFile(str(mid))
    assert len(m.tracks) == 4  # meta + 3 authors
    notes = [msg for t in m.tracks for msg in t if msg.type == "note_on"]
    assert len(notes) >= 9
    with wave.open(str(wav)) as w:
        assert w.getnframes() > w.getframerate()  # > 1 s of audio
    assert "Alice" in capsys.readouterr().out


def test_help():
    out = subprocess.run([sys.executable, str(Path(gs.__file__)), "--help"],
                         capture_output=True, text=True)
    assert out.returncode == 0 and "commit history" in out.stdout
