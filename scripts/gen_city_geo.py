"""生成 app/utils/city_geo.py —— 城市 → 经纬度对照表（供消费地图气泡图使用）

数据来源：阿里云 DataV.GeoAtlas 行政区划数据（GCJ-02 火星坐标系）中的
`properties.center` 字段，即各行政区划的官方中心点。仅包含**点位坐标**，
不包含任何行政边界线，因此不涉及地图边界合规问题。

用法（离线、一次性生成，产物已入库，日常无需重跑）：

    python scripts/gen_city_geo.py .local_tmp/city_centers.json
"""

import json
import sys
from pathlib import Path

# 直辖市：DataV 返回的是「区」（东城区…），我们需要城市本体坐标，用省级中心点
_MUNICIPAL_CENTERS = {
    "北京": [116.405285, 39.904989],
    "天津": [117.190182, 39.125596],
    "上海": [121.472644, 31.231706],
    "重庆": [106.504962, 29.533155],
}

# 港澳台：DataV 城市列表缺失，用省级中心点补齐
_SPECIAL_CENTERS = {
    "香港": [114.173355, 22.320048],
    "澳门": [113.54909, 22.198951],
    "台北": [121.509062, 25.044332],
    "高雄": [120.311922, 22.620141],
    "台中": [120.67904, 24.138347],
    "台南": [120.21201, 22.998601],
    "新北": [121.465746, 25.012366],
}

# 县级市 / 自治州：DataV 省级 _full.json 只到地级，县级市与州府坐标手工补齐
_MANUAL_CENTERS = {
    # 江苏（县级市）
    "昆山": [120.980736, 31.385588],
    "常熟": [120.752481, 31.654375],
    "张家港": [120.555386, 31.87547],
    "江阴": [120.275892, 31.921808],
    # 浙江（县级市）
    "义乌": [120.074911, 29.306863],
    "慈溪": [121.266124, 30.169653],
    "余姚": [121.154353, 30.040564],
    "诸暨": [120.246263, 29.713639],
    # 福建（县级市）
    "晋江": [118.552365, 24.781681],
    "石狮": [118.648116, 24.731983],
    # 四川（州府）
    "西昌": [102.267713, 27.881518],
    # 贵州（州府）
    "凯里": [107.981351, 26.56644],
    # 云南（州府 / 自治州）
    "大理": [100.225987, 25.589359],
    "红河": [103.384065, 23.366803],
    "西双版纳": [100.797253, 22.001724],
    "香格里拉": [99.706463, 27.826853],
    # 青海（县级市）
    "格尔木": [94.905761, 36.401555],
    # 新疆（州府 / 县级市）
    "伊犁": [81.327958, 43.916966],
    "库尔勒": [86.145779, 41.763424],
    # 吉林（州府）
    "延边": [129.509097, 42.891571],
}

# 与 region_matcher._CITY_TO_PROVINCE 对齐的「需要坐标」的城市集合，
# 由脚本在运行时从 region_matcher 导入，保证两边永不漂移。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.utils.region_matcher import _CITY_TO_PROVINCE  # noqa: E402

# 行政后缀（生成时剥掉，便于与 region_matcher 的裸词对齐）
_SUFFIXES = ("市", "地区", "自治州", "自治县", "盟")


def _strip_suffix(name: str) -> str:
    for suffix in _SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name


def _build(centers_path: Path) -> dict[str, list[float]]:
    raw = json.loads(centers_path.read_text(encoding="utf-8"))
    # 城市名（去后缀）→ 坐标；同名冲突时保留第一次出现（省会先于地级市）
    by_name: dict[str, list[float]] = {}
    for name, info in raw.items():
        key = _strip_suffix(name)
        center = info.get("center") if isinstance(info, dict) else info
        if not center or len(center) != 2:
            continue
        by_name.setdefault(key, [round(float(center[0]), 6), round(float(center[1]), 6)])

    result: dict[str, list[float]] = {}
    missing: list[str] = []
    for city in _CITY_TO_PROVINCE:
        if city in _MUNICIPAL_CENTERS:
            result[city] = _MUNICIPAL_CENTERS[city]
        elif city in _SPECIAL_CENTERS:
            result[city] = _SPECIAL_CENTERS[city]
        elif city in _MANUAL_CENTERS:
            result[city] = _MANUAL_CENTERS[city]
        elif city in by_name:
            result[city] = by_name[city]
        else:
            missing.append(city)
    return result, missing


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else ".local_tmp/city_centers.json")
    result, missing = _build(src)

    # 输出为 Python 模块：与 region_matcher 同目录，随应用一起打包（离线可用）
    lines = [
        '"""城市 → 经纬度对照表（GCJ-02），供消费地图气泡图打点使用。',
        "",
        "由 scripts/gen_city_geo.py 生成，请勿手工编辑；数据源为阿里云 DataV 行政区划",
        "数据的 center 字段（仅点位、无边界线）。键与 region_matcher._CITY_TO_PROVINCE",
        "保持一致，值格式为 [经度, 纬度]。",
        '"""',
        "",
        "CITY_CENTERS: dict[str, list[float]] = {",
    ]
    for city, coord in result.items():
        lines.append(f'    "{city}": [{coord[0]}, {coord[1]}],')
    lines.append("}")
    lines.append("")

    out = Path(__file__).resolve().parents[1] / "app" / "utils" / "city_geo.py"
    out.write_text("\n".join(lines), encoding="utf-8")

    print(f"生成 {out}：{len(result)} 个城市")
    if missing:
        print(f"缺失 {len(missing)} 个（region_matcher 有、坐标表无）：{missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
