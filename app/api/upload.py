"""账单文件导入接口（导入的账单归属当前飞牛账号）"""
from fastapi import APIRouter, Depends, File, UploadFile

from app.api.deps import GatewayUser, get_gateway_user
from app.parsers.alipay_parser import AlipayParser
from app.parsers.wechat_parser import WechatParser
from app.schemas.upload import ImportResult
from app.services import import_service

router = APIRouter(prefix="/api/upload", tags=["账单导入"])


# 同步端点：FastAPI 自动放入线程池执行，避免解析大账单时阻塞事件循环
@router.post("/wechat", response_model=ImportResult, summary="上传微信 xlsx 账单")
def upload_wechat(
    file: UploadFile = File(..., description="微信支付账单 xlsx 文件"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return import_service.import_bill_file(file, WechatParser(), ".xlsx", user.user_id)


@router.post("/alipay", response_model=ImportResult, summary="上传支付宝 csv 账单")
def upload_alipay(
    file: UploadFile = File(..., description="支付宝账单 csv 文件（GBK 编码）"),
    user: GatewayUser = Depends(get_gateway_user),
):
    return import_service.import_bill_file(file, AlipayParser(), ".csv", user.user_id)
