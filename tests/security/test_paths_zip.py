"""Path traversal, zip-slip, zip-bomb and rename-template injection."""
from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from jiangkit.security.paths import UnsafePathError, safe_extract_zip, safe_filename, safe_join

from ._util import jiang


@pytest.mark.parametrize("part", ["../x", "a/../../x", "/etc/passwd", "C:\\Windows\\x", "\\\\server\\share", "a\x00b"])
def test_safe_join_rejects_escapes(tmp_path, part):
    with pytest.raises(UnsafePathError):
        safe_join(tmp_path, part)


def test_safe_join_rejects_symlink_escape(tmp_path):
    (tmp_path / "link").symlink_to("/etc")
    with pytest.raises(UnsafePathError):
        safe_join(tmp_path, "link", "passwd")


def test_safe_join_allows_normal(tmp_path):
    assert safe_join(tmp_path, "a", "b.txt") == (tmp_path / "a" / "b.txt").resolve()


@pytest.mark.parametrize("name,expect", [("../../etc/passwd", "passwd"), ("..\\..\\boot.ini", "boot.ini"),
                                         ("...hidden", "hidden"), ("", "file"), ("a<b>:c|?.txt", "abc.txt")])
def test_safe_filename(name, expect):
    assert safe_filename(name) == expect


def _evil_zip(path: Path, members: dict[str, bytes], symlink: str | None = None):
    with zipfile.ZipFile(path, "w") as zf:
        for n, data in members.items():
            zf.writestr(n, data)
        if symlink:
            info = zipfile.ZipInfo("link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, symlink)


@pytest.mark.parametrize("member", ["../evil.txt", "../../evil.txt", "/tmp/jk_evil_abs.txt", "a/../../evil.txt"])
def test_zip_slip_blocked(tmp_path, member):
    z = tmp_path / "evil.zip"
    _evil_zip(z, {member: b"pwned"})
    dest = tmp_path / "out"
    with zipfile.ZipFile(z) as zf, pytest.raises(UnsafePathError):
        safe_extract_zip(zf, dest)
    assert not (tmp_path / "evil.txt").exists()
    assert not Path("/tmp/jk_evil_abs.txt").exists()


def test_zip_symlink_member_not_materialised(tmp_path):
    z = tmp_path / "s.zip"
    _evil_zip(z, {"ok.txt": b"fine"}, symlink="/etc/passwd")
    dest = tmp_path / "out"
    with zipfile.ZipFile(z) as zf:
        safe_extract_zip(zf, dest)
    assert (dest / "ok.txt").read_bytes() == b"fine"
    assert not (dest / "link").exists() and not (dest / "link").is_symlink()


def test_zip_bomb_limits(tmp_path):
    z = tmp_path / "bomb.zip"
    with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.bin", b"\0" * (30 * 1024 * 1024))
    with zipfile.ZipFile(z) as zf, pytest.raises(UnsafePathError):
        safe_extract_zip(zf, tmp_path / "o1")               # ratio guard
    with zipfile.ZipFile(z) as zf, pytest.raises(UnsafePathError):
        safe_extract_zip(zf, tmp_path / "o2", max_total_bytes=1024)  # total size guard
    many = tmp_path / "many.zip"
    with zipfile.ZipFile(many, "w") as zf:
        for i in range(30):
            zf.writestr(f"f{i}", b"x")
    with zipfile.ZipFile(many) as zf, pytest.raises(UnsafePathError):
        safe_extract_zip(zf, tmp_path / "o3", max_files=10)


def test_files_unzip_cli_refuses_zip_slip(tmp_path):
    z = tmp_path / "evil.zip"
    _evil_zip(z, {"../escaped.txt": b"pwned"})
    out = tmp_path / "dest"
    r = jiang("files", "files", "unzip", z, "-d", out)
    assert r.returncode != 0
    assert not (tmp_path / "escaped.txt").exists()


def test_rename_template_injection_rejected(tmp_path):
    from jiangkit.files.file_tools import plan_renames
    f = tmp_path / "a.txt"
    f.write_text("x")
    for bad in ["{name.__class__.__mro__}", "{name[0]}", "{ext.__init__.__globals__}", "{os}"]:
        with pytest.raises(ValueError):
            plan_renames([f], template=bad)
    with pytest.raises(ValueError):
        plan_renames([f], template="../{name}")
    with pytest.raises(ValueError):
        plan_renames([f], template="sub/{name}")
    assert plan_renames([f], template="{n:03d}_{name}")[0][1].name == "001_a.txt"


def test_organize_stays_inside_target(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "photo.jpg").write_bytes(b"x")
    r = jiang("files", "files", "organize", src, "--apply")
    assert r.returncode == 0, r.stderr
    for p in tmp_path.rglob("*"):
        assert src in p.parents or p == src
