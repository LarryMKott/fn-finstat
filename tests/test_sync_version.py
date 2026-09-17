"""sync_version.py 回归测试。

覆盖两块职责：

1. 参数处理：打包模式只接一个位置参数 stage_dir。
   main() 收到的是 sys.argv[1:]（不含脚本名）。历史版本把打包模式的参数
   个数误判为 2，导致正确调用 `sync_version.py <stage_dir>` 也打印用法并
   退出 1，CI 打包在版本同步一步失败、fpk 产物未生成。

2. 构建渠道派生（release / dev）：产物版本号决定文件名、包内 manifest、
   Release tag 与日志标题。dev 包若漏掉 -dev 标识就会被当成正式版分发，
   因此这层派生逻辑必须有测试兜底。
"""

import sys
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------- 渠道派生


def test_derive_version_release_keeps_base():
    """release 渠道（main 分支）产物版本号就是 VERSION 原值

    构建号与短 sha 一并忽略——正式包里不该出现构建期信息。
    """
    assert sync_version.derive_version("0.7.1", channel="release") == "0.7.1"
    assert (
        sync_version.derive_version(
            "0.7.1", channel="release", build_number="42", short_sha="1a2b3c4"
        )
        == "0.7.1"
    )


def test_derive_version_defaults_to_release_channel():
    """不传渠道时按正式版处理，兼容既有调用方与旧流水线"""
    assert sync_version.derive_version("0.7.1") == "0.7.1"


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"build_number": "42", "short_sha": "1a2b3c4"}, "0.7.1-dev.42.g1a2b3c4"),
        ({"build_number": "42"}, "0.7.1-dev.42"),
        ({"short_sha": "1a2b3c4"}, "0.7.1-dev.g1a2b3c4"),
    ],
)
def test_derive_version_dev_appends_prerelease(kwargs, expected):
    """dev 渠道追加 semver 预发布段；缺省的片段整体省略（不留空标识符）"""
    assert sync_version.derive_version("0.7.1", channel="dev", **kwargs) == expected


def test_derive_version_dev_without_identifiers():
    """本地既无构建号也无 git 信息时退化为 -dev，仍是合法 semver"""
    assert sync_version.derive_version("0.7.1", channel="dev") == "0.7.1-dev"


def test_derive_version_is_semver_lowercase_than_release():
    """预发布段必须让测试版在 semver 上小于同号正式版

    设备端据此才能从 0.7.1-dev.x 正常升级到正式 0.7.1；若改用 `+` 构建元数据，
    semver 比较时会忽略它，两者等值，升级路径就不可预期了。
    """
    assert "-" in sync_version.derive_version("0.7.1", channel="dev", build_number="1")
    assert "+" not in sync_version.derive_version(
        "0.7.1", channel="dev", build_number="1", short_sha="1a2b3c4"
    )


def test_derive_version_rejects_unknown_channel():
    """渠道写错必须抛错，不能静默产出正式版包"""
    with pytest.raises(ValueError):
        sync_version.derive_version("0.7.1", channel="canary")


def test_derive_version_sanitizes_identifiers():
    """CI 环境变量内容不完全可控，非法字符要净化，避免污染文件名与 Release tag"""
    got = sync_version.derive_version(
        "0.7.1", channel="dev", build_number="4 2", short_sha="1a2/b#3"
    )
    assert got == "0.7.1-dev.42.g1a2b3"


def test_print_mode_outputs_version_only(capsys):
    """--print 供 shell 用 $(...) 捕获，stdout 必须只有版本号一行"""
    assert (
        sync_version.main(["--print", "--channel", "dev", "--build-number", "42"]) == 0
    )
    assert capsys.readouterr().out == f"{sync_version.read_version()}-dev.42\n"


def test_print_mode_fails_on_unknown_channel(capsys):
    assert sync_version.main(["--print", "--channel", "canary"]) == 1
    assert "错误" in capsys.readouterr().err


# ---------------------------------------------------------------- 渠道别名


def test_channel_alias_maps_release_to_latest():
    """正式版别名是 latest（稳定下载入口），不是 release"""
    assert sync_version.channel_alias("release") == "latest"
    assert sync_version.channel_alias("dev") == "dev"


def test_channel_alias_rejects_unknown_channel():
    """别名拼错必须抛错，否则会产出 fn-finstat-xxx.fpk 这种没人认识的产物名"""
    with pytest.raises(ValueError):
        sync_version.channel_alias("canary")


def test_print_alias_mode_outputs_alias_only(capsys):
    """--print-alias 供 shell 捕获，stdout 必须只有别名一词"""
    assert sync_version.main(["--print-alias", "--channel", "release"]) == 0
    assert capsys.readouterr().out == "latest\n"
    assert sync_version.main(["--print-alias", "--channel", "dev"]) == 0
    assert capsys.readouterr().out == "dev\n"


def test_print_alias_mode_fails_on_unknown_channel(capsys):
    assert sync_version.main(["--print-alias", "--channel", "canary"]) == 1
    assert "错误" in capsys.readouterr().err


def test_latest_alias_never_enters_version():
    """latest 只能出现在文件名里，绝不能进版本号

    semver 里 `0.7.1-latest` 是预发布段，排序上小于 `0.7.1`，
    设备会把它当成比正式版更旧的版本而拒绝升级。
    """
    assert sync_version.channel_alias("release") == "latest"
    assert "latest" not in sync_version.derive_version("0.7.1", channel="release")


def test_pack_mode_writes_derived_version_to_stage_only(tmp_path, monkeypatch):
    """dev 渠道：包内写派生版本，源码树的 package.json 仍写 VERSION 原值

    package.json 若被写入 -dev 后缀，源码树会留下脏版本号，
    并与 `--check` 版本一致性门禁冲突。
    """
    synced: list[str] = []
    monkeypatch.setattr(
        sync_version, "sync_frontend_package", lambda version: synced.append(version)
    )
    stage = make_stage(tmp_path)

    assert (
        sync_version.main(
            [str(stage), "--channel", "dev", "--version", "0.7.1-dev.42.g1a2b3c4"]
        )
        == 0
    )

    manifest = (stage / "manifest").read_text(encoding="utf-8")
    assert "version=0.7.1-dev.42.g1a2b3c4" in manifest
    config = (stage / "app" / "app" / "config.py").read_text(encoding="utf-8")
    assert 'APP_VERSION = "0.7.1-dev.42.g1a2b3c4"' in config
    assert synced == [sync_version.read_version()]


def test_pack_mode_with_unknown_channel_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sync_version, "sync_frontend_package", lambda version: None)
    stage = make_stage(tmp_path)

    assert sync_version.main([str(stage), "--channel", "beta"]) == 1
    assert "错误" in capsys.readouterr().err
