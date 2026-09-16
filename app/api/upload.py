"""账单文件导入接口（导入的账单归属当前飞牛账号）

各平台解析器经 app/parsers 注册表统一获取，新增平台无需改本模块结构。
"""

from fastapi import APIRouter, Depends, File, Response, UploadFile
from pydantic import BaseModel

from app.api.deps import CurrentUser, request_db_session
from app.parsers import PARSERS, build_parser
from app.schemas.common import ApiResponse, ok
from app.schemas.upload import ImportDetail, ImportResult
from app.services import import_service
from app.utils.file_utils import content_disposition

# router 级依赖：本路由全部端点复用请求级数据库会话（见 deps.request_db_session）
router = APIRouter(
    prefix="/api/upload",
    tags=["账单导入"],
    dependencies=[Depends(request_db_session)],
)


def _import_bill(source: str, file: UploadFile, user_id: str) -> ImportResult:
    """按平台标识取注册表中的解析器与允许后缀，走通用导入管线"""
    spec = PARSERS[source]
    return import_service.import_bill_file(
        file, build_parser(source), spec.ext, user_id
    )


# 同步端点：FastAPI 自动放入线程池执行，避免解析大账单时阻塞事件循环
@router.post(
    "/wechat", response_model=ApiResponse[ImportResult], summary="上传微信 xlsx 账单"
)
def upload_wechat(
    user: CurrentUser,
    file: UploadFile = File(..., description="微信支付账单 xlsx 文件"),
):
    return ok(_import_bill("wechat", file, user.user_id))


@router.post(
    "/alipay", response_model=ApiResponse[ImportResult], summary="上传支付宝 csv 账单"
)
def upload_alipay(
    user: CurrentUser,
    file: UploadFile = File(..., description="支付宝账单 csv 文件（GBK 编码）"),
):
    return ok(_import_bill("alipay", file, user.user_id))


@router.post(
    "/jd", response_model=ApiResponse[ImportResult], summary="上传京东金融 csv 账单"
)
def upload_jd(
    user: CurrentUser, file: UploadFile = File(..., description="京东金融账单 csv 文件")
):
    return ok(_import_bill("jd", file, user.user_id))


@router.post(
    "/unionpay", response_model=ApiResponse[ImportResult], summary="上传云闪付 csv 账单"
)
def upload_unionpay(
    user: CurrentUser, file: UploadFile = File(..., description="云闪付账单 csv 文件")
):
    return ok(_import_bill("unionpay", file, user.user_id))


class _ExportDetailsReq(BaseModel):
    """导出差异报告请求：details 来自前端导入结果"""

    details: list[ImportDetail]


@router.post(
    "/export-details",
    response_class=Response,
    summary="导出导入差异报告为 CSV（REQ-ING-005）",
)
def export_details(user: CurrentUser, req: _ExportDetailsReq):
    filename, content, media_type = import_service.export_details(req.details)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": content_disposition(filename)},
    )
