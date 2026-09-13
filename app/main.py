"""财务统计 fn-finstat 应用入口

访问路径：
    接口地址前缀由向导参数 wizard_api_base_path 控制（默认 /app/fn-finstat），
    本地开发可用 API_BASE_PATH 环境变量覆盖：
        本地开发:      http://127.0.0.1:8090/  或  /{自定义前缀}/
        fnOS 统一网关:  https://<nas>/app/fn-finstat/（网关转发保留前缀）

因此所有路由同时挂载在根路径与自定义前缀下；前端运行时从页面地址自动推导接口地址，
修改前缀无需重新构建前端。
"""

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import ai, asset, bill, budget, category, nas, settings, stat, upload
from app.config import APP_VERSION, API_BASE_PATH, LOG_PATH
from app.db.base import init_db
from app.db.dao.category_dao import CategoryDAO

STATIC_DIR = Path(__file__).resolve().parent / "static"
PREFIX = API_BASE_PATH


def _setup_logging() -> None:
    """配置带运行时轮转的日志写入

    日志文件路径统一取 config.LOG_PATH（环境变量 LOG_FILE 优先，fnOS 由 cmd/main
    注入；本地默认项目根 app.log），与设置页「运行日志」查看/下载共用。
    单文件 10MB，保留 3 个备份。
    """
    handler = RotatingFileHandler(
        LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)
    for name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)


_setup_logging()


class ImmutableStaticFiles(StaticFiles):
    """内容哈希命名的静态资源：允许一年不可变缓存"""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def _serve_sw() -> FileResponse:
    """Service Worker 必须挂在应用根作用域才能控制整个页面，不能退到 /static 下"""
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-cache"},
    )


def _serve_manifest() -> FileResponse:
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


def _serve_icon(icon_name: str) -> FileResponse:
    """PWA 图标（path 参数不含 / ，天然免疫目录穿越）"""
    if not icon_name.endswith(".png"):
        raise HTTPException(status_code=404)
    path = STATIC_DIR / "icons" / icon_name
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/png")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    # 修复因直接操作数据库导致的孤儿分类（bills.category 不在 categories 表中）
    CategoryDAO.repair_orphans()
    yield


app = FastAPI(
    title="财务统计",
    description="个人收支统计应用：微信/支付宝/京东/云闪付账单导入、自动分类、流水管理与收支可视化",
    version=APP_VERSION,
    lifespan=lifespan,
)

# 根路径始终挂载（本地开发/兼容）；自定义前缀与根路径相同（如 "/"）时只挂载一次
_prefixes = ["", PREFIX] if PREFIX not in ("", "/") else [""]
for _prefix in _prefixes:
    app.include_router(upload.router, prefix=_prefix)
    app.include_router(nas.router, prefix=_prefix)
    app.include_router(bill.router, prefix=_prefix)
    app.include_router(budget.router, prefix=_prefix)
    app.include_router(asset.router, prefix=_prefix)
    app.include_router(category.router, prefix=_prefix)
    app.include_router(stat.router, prefix=_prefix)
    app.include_router(settings.router, prefix=_prefix)
    app.include_router(ai.router, prefix=_prefix)
    app.mount(
        f"{_prefix}/static",
        StaticFiles(directory=STATIC_DIR),
        name=f"static{_prefix or '-root'}",
    )
    app.mount(
        f"{_prefix}/assets",
        ImmutableStaticFiles(directory=STATIC_DIR / "assets"),
        name=f"assets{_prefix or '-root'}",
    )
    # PWA 入口文件：前端以 ./sw.js、./manifest.webmanifest 相对路径引用，
    # 从页面地址解析后落在应用根，必须在此显式提供路由
    app.add_api_route(
        f"{_prefix}/sw.js", _serve_sw, methods=["GET"], include_in_schema=False
    )
    app.add_api_route(
        f"{_prefix}/manifest.webmanifest",
        _serve_manifest,
        methods=["GET"],
        include_in_schema=False,
    )
    app.add_api_route(
        f"{_prefix}/icons/{'{icon_name}'}",
        _serve_icon,
        methods=["GET"],
        include_in_schema=False,
    )


@app.get("/", include_in_schema=False)
def index_root() -> FileResponse:
    # no-cache：index 引用带内容哈希的 assets，升级后必须取最新入口
    return FileResponse(
        STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"}
    )


if PREFIX not in ("", "/"):

    @app.get(PREFIX, include_in_schema=False)
    def index_prefix() -> RedirectResponse:
        # 无尾斜杠时重定向，保证前端相对路径（./assets/...）解析正确
        return RedirectResponse(url=f"{PREFIX}/")

    @app.get(f"{PREFIX}/", include_in_schema=False)
    def index_prefix_slash() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"}
        )
