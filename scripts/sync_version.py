"""构建打包时从 VERSION 文件同步版本号到暂存目录的 manifest 与 app/config.py

VERSION 是应用版本号的唯一真实来源；manifest 与 config.py 在仓库中可能滞后，
打包时以 VERSION 为准覆写暂存副本，确保 fpk 内版本号与 VERSION 一致。

另外提供前端 package.json 的版本同步与全仓库一致性校验：
frontend/package.json 的 version 同样跟随 VERSION，避免仓库里出现两个
互相矛盾的版本号（它不在打包暂存目录内，需单独处理）。

构建渠道（release / dev）：
    release 渠道（main 分支）产物版本号 = VERSION 原值（如 0.7.1）；
    dev 渠道（dev 分支）追加 semver 预发布段，形如 0.7.1-dev.42.g1a2b3c4，
    使测试包在文件名、包内 manifest、应用「关于」页、Release tag 上都能
    被识别为测试版本。
    预发布段之所以用 `-dev.N.gSHA` 而不是 `+` 构建元数据：
      1. semver 规定预发布版本小于同号正式版本，设备升级路径正确
         （装了 0.7.1-dev.x 之后能正常升级到正式 0.7.1）；
      2. `+` 在 URL / 文件名中需转义，会污染 Release 下载链接与 tag 名。
    注意：只有 manifest 与 config.py 用派生版本——frontend/package.json 必须
    保持 VERSION 原值，否则源码树里会留下带后缀的脏版本号，且与 --check 门禁冲突。

用法:
  python scripts/sync_version.py <stage_dir>          # 打包时同步暂存目录 + 前端
  python scripts/sync_version.py <stage_dir> --version 0.7.1-dev.42.g1a2b3c4
  python scripts/sync_version.py --print --channel dev --build-number 42 --short-sha 1a2b3c4
  python scripts/sync_version.py --print-alias --channel dev   # → dev（release → latest）
  python scripts/sync_version.py --check              # 仅校验全仓库版本号是否一致
"""

import json
import re
import sys
from pathlib import Path

VERSION_FILE = Path("VERSION")
FRONTEND_PKG = Path("frontend") / "package.json"

# 构建渠道 → 产物文件名别名。别名是"稳定下载入口"：同一渠道每次构建都覆盖同名文件，
# 于是下载链接可以长期不变。
#   latest = 最新正式版（main 分支）
#   dev    = 最新测试版（dev 分支）
# 别名**只用于文件名，绝不进版本号**——semver 里 `0.7.1-latest` 属于预发布段，
# 排序上小于 `0.7.1`，设备会把它当成比正式版更旧的版本从而拒绝升级。
CHANNEL_ALIASES = {"release": "latest", "dev": "dev"}

# 渠道枚举（顺序即 CHANNEL_ALIASES 的声明顺序）
CHANNELS = tuple(CHANNEL_ALIASES)

# semver 预发布标识符允许的字符集（[0-9A-Za-z-]，点号用作分隔符）
_PIECE_ILLEGAL_RE = re.compile(r"[^0-9A-Za-z-]")


def read_version() -> str:
    """读取 VERSION 文件，为空时终止（它是唯一的版本来源，不能缺）。"""
    if not VERSION_FILE.exists():
        print("错误：找不到 VERSION 文件", file=sys.stderr)
        sys.exit(1)
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        print("错误：VERSION 文件为空", file=sys.stderr)
        sys.exit(1)
    return version


def _clean_piece(value: str) -> str:
    """清洗版本号片段：剔除 semver 预发布段不允许的字符

    CI 环境变量（分支名、构建号、短 sha）内容不完全可控，任何非法字符都会
    污染版本号并进一步进入文件名与 Release tag，所以这里主动净化而非报错。
    """
    cleaned = _PIECE_ILLEGAL_RE.sub("", (value or "").strip())
    if cleaned != (value or "").strip():
        print(f"⚠️  版本号片段已净化：{value!r} → {cleaned!r}", file=sys.stderr)
    return cleaned


def derive_version(
    base: str,
    channel: str = "release",
    build_number: str = "",
    short_sha: str = "",
) -> str:
    """把 VERSION 的基础版本号派生成产物版本号。

    release → 原样返回 base；
    dev     → `{base}-dev.{构建号}.g{短sha}`，构建号与短 sha 均可缺省，
              缺省时对应片段整体省略，两者都缺则退化为 `{base}-dev`。

    渠道拼写错误直接抛 ValueError（而不是静默产出正式版包）。
    """
    if channel not in CHANNELS:
        raise ValueError(f"未知构建渠道：{channel!r}（可选：{'/'.join(CHANNELS)}）")
    if channel == "release":
        return base

    pieces = []
    build = _clean_piece(build_number)
    if build:
        pieces.append(build)
    sha = _clean_piece(short_sha)
    if sha:
        pieces.append(f"g{sha}")
    if not pieces:
        return f"{base}-dev"
    return f"{base}-dev." + ".".join(pieces)


def channel_alias(channel: str) -> str:
    """返回渠道对应的产物文件名别名（release → latest，dev → dev）。

    渠道拼写错误直接抛 ValueError，避免产物落成 `fn-finstat-xxx.fpk` 这种
    没人认识的别名。
    """
    try:
        return CHANNEL_ALIASES[channel]
    except KeyError:
        raise ValueError(
            f"未知构建渠道：{channel!r}（可选：{'/'.join(CHANNELS)}）"
        ) from None


def sync_stage(stage: Path, version: str) -> None:
    """把版本号覆写进打包暂存目录的 manifest 与 app/config.py。"""
    # 1. manifest（平铺 INI，key=value）
    manifest = stage / "manifest"
    text = manifest.read_text(encoding="utf-8")
    text = re.sub(r"^version=.*$", f"version={version}", text, flags=re.MULTILINE)
    manifest.write_text(text, encoding="utf-8")

    # 2. app/config.py（APP_VERSION = "0.6.0"）
    config = stage / "app" / "app" / "config.py"
    text = config.read_text(encoding="utf-8")
    text = re.sub(
        r'^APP_VERSION\s*=\s*".*?"',
        f'APP_VERSION = "{version}"',
        text,
        flags=re.MULTILINE,
    )
    config.write_text(text, encoding="utf-8")


def sync_frontend_package(version: str) -> None:
    """把版本号写回源码树中的 frontend/package.json。

    注意这里改的是**源码树**而非暂存目录——前端源码不进 fpk，但版本号必须与
    VERSION 保持一致，否则仓库里会出现两个互相矛盾的应用版本号。
    调用方传的是 VERSION 原值（不是渠道派生版本），避免把 `0.7.1-dev.x`
    这类构建期标识写进源码树。
    """
    if not FRONTEND_PKG.exists():
        return
    raw = FRONTEND_PKG.read_text(encoding="utf-8")
    data = json.loads(raw)
    if data.get("version") == version:
        return
    old = data.get("version")
    data["version"] = version
    FRONTEND_PKG.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"==> 前端 package.json 版本号：{old} → {version}")


def check() -> int:
    """校验全仓库版本号一致（用于发版前自检）。

    返回 0 = 一致；1 = 存在漂移。manifest / config.py 允许滞后（打包时会覆写），
    因此只检查 package.json 这个"会与源码一起被人看到"的副本。
    """
    version = read_version()
    ok = True

    if FRONTEND_PKG.exists():
        pkg_version = json.loads(FRONTEND_PKG.read_text(encoding="utf-8")).get(
            "version"
        )
        if pkg_version != version:
            print(
                f"❌ 版本号漂移：VERSION = {version}，"
                f"frontend/package.json = {pkg_version}",
                file=sys.stderr,
            )
            print(
                "   修复：python scripts/sync_version.py --sync-frontend",
                file=sys.stderr,
            )
            ok = False
        else:
            print(f"✅ frontend/package.json 版本号一致：{version}")

    return 0 if ok else 1


# 带取值的选项；打包模式下它们可与位置参数（stage_dir）任意顺序混用
VALUE_OPTIONS = ("--channel", "--version", "--build-number", "--short-sha")


def split_options(argv: list[str]) -> tuple[dict[str, str], list[str]]:
    """摘出 `--name value` 形式的选项，返回 (选项字典, 剩余位置参数)。"""
    values: dict[str, str] = {}
    positional: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in VALUE_OPTIONS:
            if index + 1 >= len(argv):
                print(f"错误：{token} 缺少取值", file=sys.stderr)
                sys.exit(1)
            values[token] = argv[index + 1]
            index += 2
        else:
            positional.append(token)
            index += 1
    return values, positional


def usage() -> None:
    print(
        "用法:\n"
        "  python scripts/sync_version.py <stage_dir>       # 打包时同步\n"
        "  python scripts/sync_version.py <stage_dir> --version <版本>  # 指定产物版本\n"
        "  python scripts/sync_version.py --print [--channel dev] [--build-number N]"
        " [--short-sha SHA]   # 打印派生版本号\n"
        "  python scripts/sync_version.py --print-alias [--channel dev]"
        "              # 打印渠道别名（latest / dev）\n"
        "  python scripts/sync_version.py --check           # 校验一致性\n"
        "  python scripts/sync_version.py --sync-frontend   # 仅同步前端",
        file=sys.stderr,
    )


def resolve_version(channel: str, build_number: str, short_sha: str) -> str:
    """按渠道算出产物版本号；渠道非法时打印错误并返回空串。"""
    try:
        return derive_version(
            read_version(),
            channel=channel,
            build_number=build_number,
            short_sha=short_sha,
        )
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return ""


def main(argv: list[str]) -> int:
    # 仅校验模式
    if "--check" in argv:
        return check()

    # 仅同步前端模式（发版时用，不需要 stage_dir）
    if "--sync-frontend" in argv:
        version = read_version()
        sync_frontend_package(version)
        print(f"==> 版本号同步：{version}（来源 VERSION 文件）")
        return 0

    # 别名模式：打印渠道别名（latest / dev），供打包脚本命名产物
    if "--print-alias" in argv:
        options, _ = split_options([a for a in argv if a != "--print-alias"])
        try:
            print(channel_alias(options.get("--channel", "release")))
        except ValueError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 1
        return 0

    # 计算模式：只把派生版本号打到 stdout（供 shell 用 `$(...)` 捕获）。
    # 诊断信息一律走 stderr，避免污染命令替换的结果。
    if "--print" in argv:
        options, _ = split_options([a for a in argv if a != "--print"])
        version = resolve_version(
            options.get("--channel", "release"),
            options.get("--build-number", ""),
            options.get("--short-sha", ""),
        )
        if not version:
            return 1
        print(version)
        return 0

    # 打包模式：同步暂存目录 + 前端
    options, positional = split_options(argv)
    if len(positional) != 1:
        usage()
        return 1

    channel = options.get("--channel", "release")
    explicit = options.get("--version", "")
    base = read_version()

    if explicit:
        version = explicit
    else:
        version = resolve_version(
            channel, options.get("--build-number", ""), options.get("--short-sha", "")
        )
        if not version:
            return 1

    sync_stage(Path(positional[0]), version)
    # 前端 package.json 始终跟随 VERSION 原值，不写渠道派生版本
    sync_frontend_package(base)
    print(f"==> 版本号同步：{version}" f"（渠道 {channel}，来源 VERSION 文件 {base}）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
