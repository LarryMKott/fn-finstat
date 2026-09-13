"""宿主集成声明测试

接入飞牛官方 JS SDK（@trimjs/web-app）有两个前置声明，缺任何一个 SDK 都拿不到
宿主能力，而且失败是「静默的」（运行时才报 Host bridge 错误），必须在打包前锁定：

  1. manifest 必须声明 micro_app=true
     —— 官方《调用方式》明确：未声明时页面不会按微应用环境加载，JS SDK 相关能力
        可能无法初始化。
  2. config/resource 的 api-scope 必须与实际调用的 trim scope 一致
     —— 本应用 v0.5 起读取用户个人授权目录并做路径 ACL 校验，需要
        trim.file.userAccess 与 trim.file.userAcl；缺声明会被网关拒绝。

同时锁定双模式图标资源齐全，避免改 manifest 时漏掉某一档尺寸。
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MANIFEST = ROOT / "manifest"
RESOURCE = ROOT / "config" / "resource"
UI_CONFIG = ROOT / "app" / "ui" / "config"
UI_IMAGES = ROOT / "app" / "ui" / "images"


def read_manifest() -> dict:
    """manifest 是 key=value 的 ini 风格文本，解析成字典"""
    data = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        data[key.strip()] = value.strip()
    return data


def test_manifest_declares_micro_app():
    """调用 JS SDK 的前提：micro_app=true"""
    manifest = read_manifest()
    assert manifest.get("micro_app") == "true", "缺少 micro_app=true，JS SDK 不会初始化"


def test_manifest_iframe_entry_present():
    """SDK 只支持 Web 宿主环境，入口必须是 iframe 类型"""
    cfg = json.loads(UI_CONFIG.read_text(encoding="utf-8"))
    entry = cfg[".url"]["fn-finstat.main"]
    assert entry["type"] == "iframe", "非 iframe 入口无法获得 Web 宿主能力"
    assert entry["url"].startswith("/app/"), "入口应走统一网关路径"


def test_resource_is_valid_and_scopes_match_trim_calls():
    """resource 合法，且 api-scope 与 trim_gateway 实际调用的 scope 严格一致

    两个 scope 均出自官方《用户个人授权路径》/《文件权限检查》文档：
      - trim.file.userAccess —— pickUserFile / trim.file.getUserAccessibleFolders
      - trim.file.userAcl    —— trim.file.checkUserACL
    多声明会在安装时向用户索要多余权限；少声明则真机上调用被网关拒绝
    （表现为 available=false，授权区不显示），故必须不多不少。
    """
    data = json.loads(RESOURCE.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "resource 顶层应为对象"
    assert "api-scope" in data, "应显式声明 api-scope"
    assert set(data["api-scope"]) == {
        "trim.file.userAccess",
        "trim.file.userAcl",
    }, "api-scope 应与 trim_gateway 实际调用的 scope 严格一致，不多不少"


def test_dual_mode_icons_complete():
    """日间/夜间两套图标在 manifest 里都要有映射，且文件真实存在"""
    cfg = json.loads(UI_CONFIG.read_text(encoding="utf-8"))
    entry = cfg[".url"]["fn-finstat.main"]

    for field in ("icon", "iconLight", "iconDark"):
        assert field in entry, f"ui/config 缺少 {field} 映射"
        # 形如 images/icon_light_{0}.png，{0} 由飞牛按尺寸替换
        template = entry[field]
        assert "{0}" in template, f"{field} 应使用 {{0}} 占位符适配多尺寸"

    # 两套图标各两档尺寸（64 / 256）都要落盘
    for name in (
        "icon_light_64.png",
        "icon_light_256.png",
        "icon_dark_64.png",
        "icon_dark_256.png",
        "icon_64.png",
        "icon_256.png",
    ):
        path = UI_IMAGES / name
        assert path.exists(), f"缺少图标文件 {name}"
        assert path.stat().st_size > 0, f"图标文件为空 {name}"


def test_frontend_depends_on_official_sdk():
    """前端依赖里必须有官方 SDK；漏装会在运行时才暴露"""
    pkg = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    deps = pkg.get("dependencies", {})
    assert "@trimjs/web-app" in deps, "缺少官方 SDK 依赖 @trimjs/web-app"
