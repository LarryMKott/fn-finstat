"""智能分类（DeepSeek）接口：配置管理、连通性测试、存量流水批量归类"""

from typing import Optional

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import GatewayUser, get_gateway_user, require_admin
from app.config import (
    AI_DEFAULT_BASE_URL,
    AISettings,
    load_ai_settings,
    save_ai_settings,
)
from app.core.errors import ValidationError
from app.schemas.ai import (
    AIClassifyRequest,
    AIClassifyResult,
    AIConfigOut,
    AIConfigUpdate,
    AIReportArchiveOut,
    AIReportArchiveRequest,
    AIReportDetail,
    AIReportGenerateRequest,
    AIReportGenerateResult,
    AIReportRequest,
    AIReportResult,
    AITestResult,
)
from app.schemas.common import ApiResponse, ok
from app.services import ai_service

router = APIRouter(prefix="/api/ai", tags=["智能分类"])


def _config_out(settings: AISettings) -> AIConfigOut:
    key = settings.api_key.strip()
    return AIConfigOut(
        enabled=settings.enabled,
        has_api_key=bool(key),
        api_key_hint=f"****{key[-4:]}" if key else "",
        base_url=settings.base_url,
        model=settings.model,
    )


def _apply_form(settings: AISettings, payload: AIConfigUpdate) -> None:
    """把设置页表单值合并进当前配置（密钥 None=不变、空串=清除）"""
    if payload.api_key is not None:
        settings.api_key = payload.api_key.strip()
    if payload.base_url is not None:
        base_url = payload.base_url.strip().rstrip("/")
        if base_url and not base_url.lower().startswith(("http://", "https://")):
            raise ValidationError("API 地址必须以 http:// 或 https:// 开头")
        settings.base_url = base_url or AI_DEFAULT_BASE_URL
    if payload.model is not None:
        model = payload.model.strip()
        if not model:
            raise ValidationError("模型名称不能为空")
        settings.model = model
    if payload.enabled is not None:
        settings.enabled = payload.enabled


@router.get(
    "/config",
    response_model=ApiResponse[AIConfigOut],
    summary="当前智能分类配置（密钥掩码）",
)
def get_config(user: GatewayUser = Depends(get_gateway_user)):
    return ok(_config_out(load_ai_settings()))


@router.put(
    "/config",
    response_model=ApiResponse[AIConfigOut],
    summary="保存智能分类配置（仅管理员：Key 为应用级共享）",
)
def update_config(payload: AIConfigUpdate, user: GatewayUser = Depends(require_admin)):
    settings = load_ai_settings()
    _apply_form(settings, payload)
    save_ai_settings(settings)
    return ok(_config_out(settings))


@router.post(
    "/test", response_model=ApiResponse[AITestResult], summary="测试 DeepSeek 连通性"
)
def test_ai_connection(
    payload: AIConfigUpdate, user: GatewayUser = Depends(get_gateway_user)
):
    """用表单当前值验证连通性；表单密钥未填时回退已保存的密钥（路由与业务函数不同名，避免同名调用误读为递归）"""
    settings = load_ai_settings()
    _apply_form(settings, payload)
    if not settings.ready:
        return ok(AITestResult(ok=False, message="请先填写 DeepSeek API Key"))
    return ok(ai_service.test_connection(settings))


@router.post(
    "/classify",
    response_model=ApiResponse[AIClassifyResult],
    summary="AI 重新归类当前账号存量流水",
)
def classify_bills(
    payload: AIClassifyRequest, user: GatewayUser = Depends(get_gateway_user)
):
    """按 scope 把当前账号流水交给 DeepSeek 重新归类（unmatched 默认只处理「其他」）"""
    return ok(ai_service.reclassify_bills(user.user_id, payload.scope))


@router.post(
    "/report",
    response_model=ApiResponse[AIReportResult],
    summary="AI 生成月度消费分析报告（旧接口，兼容）",
)
def generate_report(
    payload: AIReportRequest, user: GatewayUser = Depends(get_gateway_user)
):
    """按月汇总当前账号收支数据交给 DeepSeek 生成 Markdown 消费分析报告（默认上个月）

    保留此接口供已发布的旧客户端调用；新客户端请改用 POST /api/ai/report/generate
    以支持月/季/半年/年四种周期，并能进一步归档。
    """
    return ok(ai_service.generate_month_report(user.user_id, payload.month))


# ---- 周期报告扩展（月/季/半年/年）+ 归档 ----


@router.post(
    "/report/generate",
    response_model=ApiResponse[AIReportGenerateResult],
    summary="AI 生成周期消费分析报告（生成预览，不落库）",
)
def generate_period_report(
    payload: AIReportGenerateRequest,
    user: GatewayUser = Depends(get_gateway_user),
):
    """按 period_type（month/quarter/half/year）生成 Markdown 消费分析报告

    生成请求产生 DeepSeek API 费用；返回的 context 为后端计算的统计上下文，
    前端归档时原样回传 stats_summary 字段，无需再算一次。
    """
    return ok(
        ai_service.generate_report(
            user.user_id, payload.period_type, payload.period_value
        )
    )


@router.post(
    "/report/archive",
    response_model=ApiResponse[AIReportArchiveOut],
    summary="归档报告（按周期唯一键覆盖旧版本）",
)
def archive_report(
    payload: AIReportArchiveRequest,
    user: GatewayUser = Depends(get_gateway_user),
):
    """把生成预览得到的报告落库；按 (user_id, period_type, period_value) 唯一键 upsert

    重新归档同周期会覆盖旧版本，符合「该周期的最新快照」语义。
    """
    return ok(
        ai_service.archive_report(
            user_id=user.user_id,
            period_type=payload.period_type,
            period_value=payload.period_value,
            title=payload.title,
            content=payload.content,
            stats_summary=payload.stats_summary,
        )
    )


@router.get(
    "/report/list",
    response_model=ApiResponse[list[AIReportArchiveOut]],
    summary="归档报告列表（当前账号）",
)
def list_archived_reports(
    period_type: Optional[str] = Query(
        None, description="按周期类型过滤：month/quarter/half/year"
    ),
    user: GatewayUser = Depends(get_gateway_user),
):
    """仅返回列表项元数据（不含 Markdown 正文）；按周期类型升序、更新时间倒序"""
    return ok(ai_service.list_archived(user.user_id, period_type))


@router.get(
    "/report/{report_id}",
    response_model=ApiResponse[AIReportDetail],
    summary="归档报告详情（含 Markdown 正文）",
)
def get_archived_report(
    report_id: int = Path(..., description="归档报告 id"),
    user: GatewayUser = Depends(get_gateway_user),
):
    """查看归档报告（不消耗 DeepSeek 配额）；不存在或跨账号返回 404"""
    return ok(ai_service.get_archived(user.user_id, report_id))


@router.delete(
    "/report/{report_id}",
    response_model=ApiResponse[dict],
    summary="删除归档报告",
)
def delete_archived_report(
    report_id: int = Path(..., description="归档报告 id"),
    user: GatewayUser = Depends(get_gateway_user),
):
    """删除归档报告（仅当前账号）；不存在返回 ok=False"""
    return ok({"ok": ai_service.delete_archived(user.user_id, report_id)})
