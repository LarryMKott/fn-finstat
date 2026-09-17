"""sync_version.py 参数处理回归测试：打包模式只接一个位置参数 stage_dir。

main() 收到的是 sys.argv[1:]（不含脚本名）。历史版本把打包模式的参数
个数误判为 2，导致正确调用 `sync_version.py <stage_dir>` 也打印用法并
退出 1，CI 打包在版本同步一步失败、fpk 产物未生成。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sync_version  # noqa: E402


def make_stage(tmp_path: Path) -> Path:
    stage = tmp_path / "fpk-stage"
    (stage / "app" / "app").mkdir(parents=True)
    (stage / "manifest").write_text(
        "appname=fn-finstat\nversion=0.0.0\n", encoding="utf-8"
    )
    (stage / "app" / "app" / "config.py").write_text(
        'APP_VERSION = "0.0.0"\n', encoding="utf-8"
    )
    return stage


def test_pack_mode_accepts_single_stage_dir(tmp_path, monkeypatch):
    # 不触碰源码树中的 frontend/package.json，保持测试无副作用
    monkeypatch.setattr(sync_version, "sync_frontend_package", lambda version: None)
    stage = make_stage(tmp_path)

    assert sync_version.main([str(stage)]) == 0

    version = sync_version.read_version()
    manifest = (stage / "manifest").read_text(encoding="utf-8")
    assert f"version={version}" in manifest
    config = (stage / "app" / "app" / "config.py").read_text(encoding="utf-8")
    assert f'APP_VERSION = "{version}"' in config


def test_no_args_fails_with_usage(capsys):
    assert sync_version.main([]) == 1
    assert "用法" in capsys.readouterr().err


def test_extra_positional_args_fail_with_usage(capsys):
    assert sync_version.main(["a", "b"]) == 1
    assert "用法" in capsys.readouterr().err
