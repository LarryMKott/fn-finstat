"""财务统计 fn-finstat 应用入口

访问路径：
    接口地址前缀由向导参数 wizard_api_base_path 控制（默认 /app/fn-finstat），
    本地开发可用 API_BASE_PATH 环境变量覆盖：
        本地开发:      http://127.0.0.1:8090/  或  /{自定义前缀}/
        fnOS 统一网关:  https://<nas>/app/fn-finstat/（网关转发保留前缀）

因此所有路由同时挂载在根路径与自定义前缀下；前端运行时从页面地址自动推导接口地址，
修改前缀无需重新构建前端。
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import bill, category, stat, upload
from app.config import API_BASE_PATH
from app.db.base import init_db

STATIC_DIR = Path(__file__).resolve().parent / "static"
PREFIX = API_BASE_PATH


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="财务统计",
    description="个人收支统计应用：微信/支付宝账单导入、自动分类、流水管理与收支可视化",
    version="0.1.0",
    lifespan=lifespan,
)

# 根路径始终挂载（本地开发/兼容）；自定义前缀与根路径相同（如 "/"）时只挂载一次
_prefixes = ["", PREFIX] if PREFIX not in ("", "/") else [""]
for _prefix in _prefixes:
    app.include_router(upload.router, prefix=_prefix)
    app.include_router(bill.router, prefix=_prefix)
    app.include_router(category.router, prefix=_prefix)
    app.include_router(stat.router, prefix=_prefix)
    app.mount(f"{_prefix}/static", StaticFiles(directory=STATIC_DIR), name=f"static{_prefix or '-root'}")
    app.mount(f"{_prefix}/assets", StaticFiles(directory=STATIC_DIR / "assets"), name=f"assets{_prefix or '-root'}")


@app.get("/", include_in_schema=False)
def index_root() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


if PREFIX not in ("", "/"):
    @app.get(PREFIX, include_in_schema=False)
    def index_prefix() -> RedirectResponse:
        # 无尾斜杠时重定向，保证前端相对路径（./assets/...）解析正确
        return RedirectResponse(url=f"{PREFIX}/")

    @app.get(f"{PREFIX}/", include_in_schema=False)
    def index_prefix_slash() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")
