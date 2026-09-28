"""智能分类（AI 多供应商）接口：配置管理、连通性测试、存量流水批量归类"""

from typing import Optional

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import AdminUser, CurrentUser, request_db_session
from app.file_settings import (
    AI_DEFAULT_BASE_URL,
    AI_PROVIDERS,
    AISettings,
    ai_provider_default,
    ai_provider_label,
    load_ai_settings,
    save_ai_settings,
)
from app.core.errors import ValidationError
from app.schemas.ai import (
    AIConfigOut,
    AIConfigUpdate,
    AIProviderInfo,
    AIClassifyRequest,
    AIClassifyResult,
    AIChildrenApplyRequest,
    AIChildrenApplyResult,
    AIChildrenGenerateRequest,
    AIChildrenGenerateResult,
    AIKeywordApplyRequest,
    AIKeywordGenerateRequest,
    AIKeywordGenerateResult,
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
from app.services import ai_service, audit_service, auto_report_service
from app.utils.net_guard import OutboundBlockedError, validate_outbound_url

router = APIRouter(
    prefix="/api/ai",
    tags=["智能分类"],
    dependencies=[Depends(request_db_session)],
)


def _config_out(settings: AISettings, is_admin: bool = True) -> AIConfigOut:
    """配置视图：接入地址与密钥尾号仅管理员可见

    普通账号共享应用级 AI 配置（classify 直接使用存档配置），但不需要也无权
    知晓配置内容——base_url 是管理员配置的内部端点，密钥尾号可用于针对性
    枚举/钓鱼。is_admin 口径与 get_database_info 一致（无网关身份的本地/
    独立部署视为唯一用户放行）。供应商预置表为公开接入常识，仅随管理员
    视图下发（普通账号用不到）。
    """
    key = settings.api_key.strip()
    providers = []
    if is_admin:
        providers = [
            AIProviderInfo(
                value=value,
                label=info["label"],
                base_url=info["base_url"],
                models=list(info["models"]),
                key_url=info["key_url"],
            )
            for value, info in AI_PROVIDERS.items()
        ]
    return AIConfigOut(
        enabled=settings.enabled,
        auto_report_enabled=settings.auto_report_enabled,
        has_api_key=bool(key),
        provider=settings.provider,
        api_key_hint=f"****{key[-4:]}" if key and is_admin else "",
        base_url=settings.base_url if is_admin else "",
        model=settings.model,
        auto_report_generated=auto_report_service.monthly_generated(),
        providers=providers,
        auto_keyword_enabled=settings.auto_keyword_enabled,
        auto_category_enabled=settings.auto_category_enabled,
        auto_subcategory_enabled=settings.auto_subcategory_enabled,
    )


def _apply_form(settings: AISettings, payload: AIConfigUpdate) -> None:
    """把设置页表单值合并进当前配置（密钥 None=不变、空串=清除）

    切换供应商时若未显式带 base_url/model，则回填该供应商的预置默认值，
    避免出现「供应商=智谱、地址仍是 DeepSeek」的错配组合。
    """
    provider_changed = False
    if payload.provider is not None:
        provider = payload.provider.strip()
        if provider not in AI_PROVIDERS:
            raise ValidationError(f"不支持的 AI 供应商：{provider}")
        if provider != settings.provider:
            provider_changed = True
        settings.provider = provider
    if payload.api_key is not None:
        settings.api_key = payload.api_key.strip()
    if payload.base_url is not None:
        base_url = payload.base_url.strip().rstrip("/")
        if base_url:
            # 出站地址安全校验（SSRF）：拒内网/回环/链路本地/云元数据地址。
            # AI 通道会把 API Key 以 Bearer 头发给该地址，被诱导填入恶意地址
            # 即等于直接泄露密钥，故保存时即拦（而非等出站才失败）。
            try:
                validate_outbound_url(base_url)
            except OutboundBlockedError as exc:
                raise ValidationError(str(exc)) from exc
        settings.base_url = base_url or (
            ai_provider_default(settings.provider, "base_url") or AI_DEFAULT_BASE_URL
        )
    elif provider_changed:
        settings.base_url = (
            ai_provider_default(settings.provider, "base_url") or AI_DEFAULT_BASE_URL
        )
    if payload.model is not None:
        model = payload.model.strip()
        if not model:
            raise ValidationError("模型名称不能为空")
        settings.model = model
    elif provider_changed and not payload.model:
        default_model = ai_provider_default(settings.provider, "model")
        if default_model:
            settings.model = default_model
    if payload.enabled is not None:
        settings.enabled = payload.enabled
    if payload.auto_report_enabled is not None:
        settings.auto_report_enabled = payload.auto_report_enabled
    if payload.auto_keyword_enabled is not None:
        settings.auto_keyword_enabled = payload.auto_keyword_enabled
    if payload.auto_category_enabled is not None:
        settings.auto_category_enabled = payload.auto_category_enabled
    if payload.auto_subcategory_enabled is not None:
        settings.auto_subcategory_enabled = payload.auto_subcategory_enabled


@router.get(
    "/config",
    response_model=ApiResponse[AIConfigOut],
    summary="当前智能分类配置（密钥掩码）",
)
def get_config(user: CurrentUser):
    is_admin = not user.user_id or user.is_admin
    return ok(_config_out(load_ai_settings(), is_admin))


@router.put(
    "/config",
    response_model=ApiResponse[AIConfigOut],
    summary="保存智能分类配置（仅管理员：Key 为应用级共享）",
)
def update_config(user: AdminUser, payload: AIConfigUpdate):
    settings = load_ai_settings()
    _apply_form(settings, payload)
    save_ai_settings(settings)
    audit_service.record(
        user.user_id,
        "ai.config",
        "ai_config",
        None,
        "保存智能分类配置（provider="
        + ai_provider_label(settings.provider)
        + ", model="
        + settings.model
        + ", enabled="
        + str(settings.enabled)
        + ", auto_report="
        + str(settings.auto_report_enabled)
        + ", auto_category="
        + str(settings.auto_category_enabled)
        + "）",
    )
    return ok(_config_out(settings, is_admin=True))


@router.post(
    "/test",
    response_model=ApiResponse[AITestResult],
    summary="测试 AI 连通性（仅管理员）",
)
def test_ai_connection(user: AdminUser, payload: AIConfigUpdate):
    """用表单当前值验证连通性；表单密钥未填时回退已保存的密钥（路由与业务函数不同名，避免同名调用误读为递归）

    与保存接口同为管理员操作：base_url 由调用方提供，若对全部用户开放，
    任意账号可让服务端把共享 API Key 以 Bearer 头发往自己控制的服务器，
    Key 会被对端记录窃取。
    """
    settings = load_ai_settings()
    _apply_form(settings, payload)
    if not settings.ready:
        return ok(AITestResult(ok=False, message="请先填写 API Key"))
    return ok(ai_service.test_connection(settings))


@router.post(
    "/classify",
    response_model=ApiResponse[AIClassifyResult],
    summary="AI 重新归类当前账号存量流水",
)
def classify_bills(user: CurrentUser, payload: AIClassifyRequest):
    """按 scope 把当前账号流水交给 AI 重新归类（unmatched 默认只处理「其他」）

    scope=all 时携带上一轮返回的 next_after_id 可续跑，避免超预算后重头重复计费。
    auto_category_enabled 开启时白名单外达标提名可现场建新分类（配额护栏见服务层）。
    """
    return ok(
        ai_service.reclassify_bills(user.user_id, payload.scope, payload.after_id)
    )


# ---- 分类扩展 AI 任务（v1.1，均两段式）----
# 入口统一收口管理员：生成请求会把共享 API Key 发往外部端点（与 /test 同理），
# 且生成/落库都改写全局共享的分类体系。生成会产生 API 费用，落库不产生。


@router.post(
    "/category/keywords",
    response_model=ApiResponse[AIKeywordGenerateResult],
    summary="AI 为分类生成关键词候选（预览，不落库）",
)
def generate_category_keywords(user: AdminUser, payload: AIKeywordGenerateRequest):
    return ok(ai_service.generate_keyword_candidates(payload.category_id, payload.hint))


@router.post(
    "/category/keywords/apply",
    response_model=ApiResponse[dict],
    summary="把人工勾选的关键词候选写入分类（source=ai）",
)
def apply_category_keywords(user: AdminUser, payload: AIKeywordApplyRequest):
    result = ai_service.apply_keywords(payload.category_id, payload.keywords)
    audit_service.record(
        user.user_id,
        "category.keyword.ai_apply",
        "category",
        payload.category_id,
        f"AI 关键词候选落库 {result['added']} 个",
    )
    return ok(result)


@router.post(
    "/category/children",
    response_model=ApiResponse[AIChildrenGenerateResult],
    summary="AI 生成子类方案预览（不落库）",
)
def generate_category_children(user: AdminUser, payload: AIChildrenGenerateRequest):
    return ok(ai_service.generate_subcategory_plan(payload.category_id))


@router.post(
    "/category/children/apply",
    response_model=ApiResponse[AIChildrenApplyResult],
    summary="应用子类方案（建子分类 + 落关键词，可选迁移流水）",
)
def apply_category_children(user: AdminUser, payload: AIChildrenApplyRequest):
    result = ai_service.apply_subcategories(
        payload.category_id,
        [c.model_dump() for c in payload.children],
        payload.migrate_bills,
    )
    audit_service.record(
        user.user_id,
        "category.subcategory.apply",
        "category",
        payload.category_id,
        "AI 子类方案落库："
        + "、".join(c["name"] for c in result["created"])
        + f"，迁移 {result['migrated']} 条流水",
    )
    return ok(result)


@router.post(
    "/report",
    response_model=ApiResponse[AIReportResult],
    summary="AI 生成月度消费分析报告（旧接口，兼容）",
)
def generate_report(user: CurrentUser, payload: AIReportRequest):
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
def generate_period_report(user: CurrentUser, payload: AIReportGenerateRequest):
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
def archive_report(user: CurrentUser, payload: AIReportArchiveRequest):
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
    user: CurrentUser,
    period_type: Optional[str] = Query(
        None, description="按周期类型过滤：month/quarter/half/year"
    ),
):
    """仅返回列表项元数据（不含 Markdown 正文）；按周期类型升序、更新时间倒序"""
    return ok(ai_service.list_archived(user.user_id, period_type))


@router.get(
    "/report/{report_id}",
    response_model=ApiResponse[AIReportDetail],
    summary="归档报告详情（含 Markdown 正文）",
)
def get_archived_report(
    user: CurrentUser, report_id: int = Path(..., description="归档报告 id")
):
    """查看归档报告（不消耗 DeepSeek 配额）；不存在或跨账号返回 404"""
    return ok(ai_service.get_archived(user.user_id, report_id))


@router.delete(
    "/report/{report_id}",
    response_model=ApiResponse[dict],
    summary="删除归档报告",
)
def delete_archived_report(
    user: CurrentUser, report_id: int = Path(..., description="归档报告 id")
):
    """删除归档报告（仅当前账号）；不存在返回 ok=False"""
    return ok({"ok": ai_service.delete_archived(user.user_id, report_id)})
