import json
import os
import time
import zipfile

from jiangkit.files import file_tools


def touch(p, content="x", mtime=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


def test_rename_dry_run_then_apply(cli, tmp_path):
    d = tmp_path / "imgs"
    for n in ["IMG_10.png", "IMG_2.png", "IMG_1.png", "notes.txt"]:
        touch(d / n)
    res = cli("file_tools", "rename", d, "-t", "mei_{n:03d}", "-g", "*.png")
    assert "IMG_1.png  ->  mei_001.png" in res.stdout and "[dry-run]" in res.stdout
    assert (d / "IMG_1.png").exists()  # nothing changed yet
    cli("file_tools", "rename", d, "-t", "mei_{n:03d}", "-g", "*.png", "--apply")
    names = sorted(p.name for p in d.iterdir() if not p.name.startswith("rename_log"))
    assert names == ["mei_001.png", "mei_002.png", "mei_003.png", "notes.txt"]
    assert list(d.glob("rename_log_*.csv"))


def test_rename_regex_and_collision(cli, tmp_path):
    d = tmp_path / "r"
    touch(d / "ep01 draft.srt")
    touch(d / "ep02 draft.srt")
    cli("file_tools", "rename", d, "--find", r"ep(\d+) draft", "--replace", r"EP\1_final", "--apply")
    assert (d / "EP01_final.srt").exists() and (d / "EP02_final.srt").exists()
    # A template without {n} would map both files to one name -> refused
    res = cli("file_tools", "rename", d, "-t", "same", "-g", "*.srt", check=False)
    assert res.returncode != 0 and "duplicate" in res.stderr


def test_organize_by_type_and_date(cli, tmp_path):
    d = tmp_path / "dl"
    touch(d / "a.jpg"); touch(d / "b.mp4"); touch(d / "c.pdf"); touch(d / "d.xyz")
    cli("file_tools", "organize", d)
    assert (d / "a.jpg").exists()
    cli("file_tools", "organize", d, "--apply")
    assert (d / "Images/a.jpg").exists() and (d / "Videos/b.mp4").exists()
    assert (d / "Documents/c.pdf").exists() and (d / "Other/d.xyz").exists()

    d2 = tmp_path / "dated"
    t = time.mktime((2025, 3, 15, 12, 0, 0, 0, 0, -1))
    touch(d2 / "old.png", mtime=t)
    cli("file_tools", "organize", d2, "--by", "date", "--apply")
    assert (d2 / "2025/2025-03/old.png").exists()


def test_dupes(cli, tmp_path):
    d = tmp_path / "dup"
    touch(d / "a.txt", "same content"); touch(d / "sub/b.txt", "same content")
    touch(d / "c.txt", "different!!!!"); touch(d / "e.txt", "same contenT")
    groups = file_tools.find_duplicates(d)
    assert len(groups) == 1 and len(groups[0]["files"]) == 2
    cli("file_tools", "dupes", d, "--json", "d.json", "--move-to", "trash", "--apply")
    assert len(json.loads((tmp_path / "d.json").read_text())) == 1
    assert len(list((tmp_path / "trash").iterdir())) == 1


def test_du(cli, tmp_path):
    d = tmp_path / "du"
    touch(d / "big/huge.bin", "x" * 50000); touch(d / "small.txt", "x" * 10)
    res = cli("file_tools", "du", d, "-n", "3", "--csv", "du.csv")
    assert "huge.bin" in res.stdout.split("largest folders")[0]
    files, dirs = file_tools.disk_usage(d)
    assert dirs[d] == 50010 and dirs[d / "big"] == 50000


def test_zip_unzip_plain_and_password(cli, tmp_path):
    src = tmp_path / "proj"
    touch(src / "a.txt", "hello"); touch(src / "sub/b.txt", "world")
    cli("file_tools", "zip", src, "-o", "plain.zip")
    assert sorted(zipfile.ZipFile(tmp_path / "plain.zip").namelist()) == ["proj/a.txt", "proj/sub/b.txt"]
    env = {"ZIPPW": "s3cret-密碼"}
    cli("file_tools", "zip", src, "-o", "enc.zip", "--password-env", "ZIPPW", extra_env=env)
    bad = cli("file_tools", "unzip", "enc.zip", "-d", "x", check=False)
    assert bad.returncode != 0
    cli("file_tools", "unzip", "enc.zip", "-d", "out", "--password-env", "ZIPPW", extra_env=env)
    assert (tmp_path / "out/proj/sub/b.txt").read_text() == "world"


def test_unzip_blocks_zip_slip(cli, tmp_path):
    with zipfile.ZipFile(tmp_path / "evil.zip", "w") as z:
        z.writestr("../escape.txt", "boom")
    res = cli("file_tools", "unzip", "evil.zip", "-d", "safe", check=False)
    assert res.returncode != 0 and not (tmp_path / "escape.txt").exists()


def test_incremental_backup(cli, tmp_path):
    src, dst = tmp_path / "src", tmp_path / "bak"
    touch(src / "a.txt", "1"); touch(src / "d/b.txt", "2"); touch(src / "skip.tmp", "t")
    r1 = cli("file_tools", "backup", src, dst, "--exclude", "*.tmp")
    assert "2 copied, 0 unchanged" in r1.stdout
    assert not (dst / "skip.tmp").exists()
    r2 = cli("file_tools", "backup", src, dst, "--exclude", "*.tmp")
    assert "0 copied, 2 unchanged" in r2.stdout
    time.sleep(0.01)
    touch(src / "a.txt", "changed", mtime=time.time() + 5)
    (src / "d/b.txt").unlink()
    r3 = cli("file_tools", "backup", src, dst, "--exclude", "*.tmp", "--delete")
    assert "1 copied" in r3.stdout and "1 removed" in r3.stdout
    assert (dst / "a.txt").read_text() == "changed"
    assert not (dst / "d/b.txt").exists() and list((dst / ".deleted").rglob("b.txt"))
    m = json.loads((dst / file_tools.MANIFEST).read_text())
    assert "a.txt" in m["files"] and len(m["history"]) == 3
