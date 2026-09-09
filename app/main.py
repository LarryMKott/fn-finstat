"""财务统计 fn-finstat 应用入口

访问路径：
    本地开发:      http://127.0.0.1:8090/  或  /app/fn-finstat/
    fnOS 统一网关:  https://<nas>/app/fn-finstat/（网关转发保留前缀，见 gateway-registration.md）

因此所有路由同时挂载在根路径与网关前缀下，两种入口行为完全一致。
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import bill, category, stat, upload
from app.db.base import init_db

STATIC_DIR = Path(__file__).resolve().parent / "static"
# 与 app/ui/config 的 gatewayPrefix 保持一致
GATEWAY_PREFIX = "/app/fn-finstat"


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

for prefix in ("", GATEWAY_PREFIX):
    app.include_router(upload.router, prefix=prefix)
    app.include_router(bill.router, prefix=prefix)
    app.include_router(category.router, prefix=prefix)
    app.include_router(stat.router, prefix=prefix)
    app.mount(f"{prefix}/static", StaticFiles(directory=STATIC_DIR), name=f"static{prefix or '-root'}")
    app.mount(f"{prefix}/assets", StaticFiles(directory=STATIC_DIR / "assets"), name=f"assets{prefix or '-root'}")


@app.get("/", include_in_schema=False)
@app.get(GATEWAY_PREFIX, include_in_schema=False)
@app.get(f"{GATEWAY_PREFIX}/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")
