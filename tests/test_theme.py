"""宿主（飞牛 fnOS）主题透传测试

飞牛在 iframe 里嵌本应用时，前端主要靠读 localStorage 的 fnos-theme-mode 判断
日间/夜间；跨域场景读不到，退化为由网关把主题经请求头转发下来，本应用再通过
/api/settings/about 回传给前端。这组用例锁定该透传链路的解析与降级行为。
"""

from app.api.deps import get_gateway_user
from app.core.context import normalize_theme
from tests.conftest import USER_A


def test_normalize_accepts_fnos_numeric_modes():
    """飞牛的 fnos-theme-mode 取值：10 = 日间，20 = 夜间"""
    assert normalize_theme("10") == "light"
    assert normalize_theme("20") == "dark"


def test_normalize_accepts_word_forms():
    assert normalize_theme("light") == "light"
    assert normalize_theme("DAY") == "light"
    assert normalize_theme("dark") == "dark"
    assert normalize_theme(" Night ") == "dark"


def test_normalize_rejects_unknown_and_empty():
    """识别不出的一律返回空串，让前端回退到自己的探测链路"""
    assert normalize_theme(None) == ""
    assert normalize_theme("") == ""
    assert normalize_theme("   ") == ""
    assert normalize_theme("auto") == ""
    assert normalize_theme("system") == ""


def test_deps_reads_theme_from_each_candidate_header():
    """三种候选头名任一存在即可，顺序为 X-Trim-Theme > X-Fnos-Theme > X-Trim-Theme-Mode

    直接调用函数时需把全部参数显式传入：未传的参数会保留 Header(...) 描述符本身
    （FastAPI 只在经过依赖注入时才把它们解析为值）。
    """
    def call(**kwargs):
        base = {
            "x_trim_userid": None,
            "x_trim_username": None,
            "x_trim_isadmin": None,
            "x_trim_theme": None,
            "x_fnos_theme": None,
            "x_trim_theme_mode": None,
        }
        return get_gateway_user(**{**base, **kwargs})

    assert call(x_trim_theme="20").theme_raw == "20"
    assert call(x_fnos_theme="dark").theme_raw == "dark"
    assert call(x_trim_theme_mode="10").theme_raw == "10"
    # 前一个为空串时不阻断后面的候选
    assert call(x_trim_theme="", x_fnos_theme="20").theme_raw == "20"
    # 优先级：X-Trim-Theme 压过其余两个
    assert call(x_trim_theme="20", x_fnos_theme="10").theme_raw == "20"


def test_deps_theme_absent_is_empty_string():
    """无网关（本地开发、独立部署）时为空串，前端据此判断回退"""
    user = get_gateway_user(
        x_trim_userid=USER_A,
        x_trim_username=None,
        x_trim_isadmin=None,
        x_trim_theme=None,
        x_fnos_theme=None,
        x_trim_theme_mode=None,
    )
    assert user.user_id == USER_A
    assert user.theme_raw == ""


def test_about_endpoint_passes_through_theme(client):
    """网关带上主题头时，/about 原样透传（保持原始写法，归一化交给前端）"""
    resp = client.get(
        "/api/settings/about",
        headers={"X-Trim-Userid": USER_A, "X-Trim-Theme": "20"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["fnos_theme"] == "20"


def test_about_endpoint_without_theme_header(client):
    """无主题头时字段为空串，不应报错也不应省略字段"""
    resp = client.get("/api/settings/about", headers={"X-Trim-Userid": USER_A})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["fnos_theme"] == ""
    # 既有字段不受影响
    assert data["app_name"]
    assert data["version"]
