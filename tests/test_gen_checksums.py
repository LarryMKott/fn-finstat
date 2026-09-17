"""gen_checksums.py 的单元测试。

这个脚本产出的是**发给用户的校验文件**：格式错一行，用户执行
`md5sum -c MD5SUMS.txt` 就会全校验失败；更糟的是缺产物时静默少一行，
用户会以为是自己下载坏了。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gen_checksums  # noqa: E402


def test_md5_matches_known_digests(tmp_path):
    """校验值与标准答案一致（空文件 / 有内容文件各一例）"""
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    assert gen_checksums.md5_of(empty) == "d41d8cd98f00b204e9800998ecf8427e"

    payload = tmp_path / "abc.bin"
    payload.write_bytes(b"abc")
    assert gen_checksums.md5_of(payload) == "900150983cd24fb0d6963f7d28e17f72"


def test_lines_use_coreutils_format_and_are_sorted(tmp_path):
    """每行必须是 `<md5><两个空格><文件名>`，且按文件名排序（输出稳定、便于比对）"""
    (tmp_path / "b.fpk").write_bytes(b"beta")
    (tmp_path / "a.fpk").write_bytes(b"alpha")

    lines = gen_checksums.build_lines([tmp_path / "b.fpk", tmp_path / "a.fpk"])

    assert lines == [
        f"{gen_checksums.md5_of(tmp_path / 'a.fpk')}  a.fpk",
        f"{gen_checksums.md5_of(tmp_path / 'b.fpk')}  b.fpk",
    ]
    for line in lines:
        digest, _, name = line.partition("  ")
        assert len(digest) == 32
        assert digest == digest.lower()
        assert name and "/" not in name and "\\" not in name


def test_missing_artifact_raises(tmp_path):
    """缺产物必须报错，不能产出"部分校验"文件骗过用户"""
    (tmp_path / "ok.fpk").write_bytes(b"x")

    with pytest.raises(FileNotFoundError):
        gen_checksums.build_lines([tmp_path / "ok.fpk", tmp_path / "missing.fpk"])


def test_main_writes_lf_only_file(tmp_path, capsys, monkeypatch):
    """输出必须是 LF 换行：CRLF 会让 `md5sum -c` 把 \\r 当成文件名的一部分"""
    (tmp_path / "app.fpk").write_bytes(b"content")
    out = tmp_path / "MD5SUMS.txt"
    monkeypatch.chdir(tmp_path)

    assert gen_checksums.main(["-o", str(out), "app.fpk"]) == 0

    raw = out.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    assert (tmp_path / "app.fpk").name in raw.decode("utf-8")


def test_main_fails_on_missing_file(tmp_path, capsys, monkeypatch):
    """产物缺失时既要失败，也不许留下半成品校验文件"""
    monkeypatch.chdir(tmp_path)

    assert gen_checksums.main(["-o", "MD5SUMS.txt", "nope.fpk"]) == 1

    assert "错误" in capsys.readouterr().err
    assert not (tmp_path / "MD5SUMS.txt").exists()
