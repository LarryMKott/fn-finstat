"""账单文件导入接口"""
from fastapi import APIRouter, File, UploadFile

from app.parsers.alipay_parser import AlipayParser
from app.parsers.wechat_parser import WechatParser
from app.schemas.upload import ImportResult
from app.services import import_service

router = APIRouter(prefix="/api/upload", tags=["账单导入"])


@router.post("/wechat", response_model=ImportResult, summary="上传微信 xlsx 账单")
async def upload_wechat(file: UploadFile = File(..., description="微信支付账单 xlsx 文件")):
    return import_service.import_bill_file(file, WechatParser(), ".xlsx")


@router.post("/alipay", response_model=ImportResult, summary="上传支付宝 csv 账单")
async def upload_alipay(file: UploadFile = File(..., description="支付宝账单 csv 文件（GBK 编码）")):
    return import_service.import_bill_file(file, AlipayParser(), ".csv")
