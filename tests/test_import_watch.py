"""目录监听自动导入测试（T-5.3）：指纹判重、同名变更识别、失败不重试、边界安全

复用 test_nas.py 的样例账单构造；NAS 配置经 conftest 隔离到临时文件。
"""

from pathlib import Path

import pytest

from app.db.dao.task_dao import ImportedFileDAO
from app.services import import_watch_service, nas_service
from tests.test_nas import ALIPAY_ROWS, JD_ROWS, write_csv

USER = "watcher-1"


@pytest.fixture()
def watch_dir(tmp_path: Path, monkeypatch):
    """配置好的监听目录 + 归属账号（owner 写入 NAS 配置）"""
    d = tmp_path / "bills"
    d.mkdir()
    nas_service.update_config(type("P", (), {"import_dir": str(d)}), owner_user_id=USER)
    return d


def _scan():
    return import_watch_service.scan_and_import()


def test_not_configured_dir_is_successful_noop(db, tmp_path):
    """未配置目录：空转成功（返回 0），不触发调度器失败退避"""
    affected, message = _scan()
    assert affected == 0
    assert "未配置" in message


def test_new_file_imported_and_recorded(db, watch_dir):
    """新文件在一个扫描周期内入库，并登记指纹"""
    write_csv(watch_dir / "alipay.csv", ALIPAY_ROWS)
    affected, message = _scan()
    assert affected == 1
    assert "新增文件 1" in message
    record = ImportedFileDAO.get_by_path(str(watch_dir / "alipay.csv"))
    assert record["status"] == "ok"
    assert record["inserted"] == 1
    assert record["user_id"] == USER
    assert len(record["content_hash"]) == 64


def test_same_content_not_reimported(db, watch_dir):
    """同名同内容文件不重复导入（指纹一致直接跳过）"""
    write_csv(watch_dir / "alipay.csv", ALIPAY_ROWS)
    _scan()
    affected, _ = _scan()
    assert affected == 0
    # 即使文件被同名覆盖但内容相同，也不重复导入
    # 用二进制读写保证字节完全一致：write_csv 用 newline="" 产生 \r\n 换行，
    # 若用 read_text/write_text（默认 newline=None）会在 Linux 上把 \r\n 翻成 \n，
    # 字节不同 → sha256 指纹变化 → 误判为"变更文件"，违背"同内容"的测试意图
    (watch_dir / "alipay.csv").write_bytes((watch_dir / "alipay.csv").read_bytes())
    affected, message = _scan()
    assert affected == 0
    assert "未变化 1" in message


def test_replaced_file_with_new_content_reimported(db, watch_dir):
    """同名文件内容变化（重新导出覆盖）识别为变更并导入新流水"""
    write_csv(watch_dir / "alipay.csv", ALIPAY_ROWS)
    _scan()
    rows = [row.copy() for row in ALIPAY_ROWS]
    rows[2] = rows[2].copy()
    rows[2][9] = "NAS-ALI-0002"  # 换交易号
    rows[2][6] = "88.00"  # 换金额
    write_csv(watch_dir / "alipay.csv", rows)
    affected, message = _scan()
    assert affected == 1
    assert "变更文件 1" in message


def test_unrecognized_file_recorded_once(db, watch_dir):
    """无法识别来源的文件登记为 unknown，同内容不反复处理"""
    (watch_dir / "random.csv").write_text("姓名,电话\n张三,123\n", encoding="utf-8")
    affected, _ = _scan()
    assert affected == 0
    record = ImportedFileDAO.get_by_path(str(watch_dir / "random.csv"))
    assert record["status"] == "unknown"
    affected, message = _scan()
    assert affected == 0
    assert "未变化 1" in message  # 同内容第二轮按指纹判重跳过，不重复识别


def test_failed_import_not_retried_until_content_changes(db, watch_dir):
    """导入失败的文件记录原因，同内容不再重试；内容变化后重新处理（恢复后成功）"""
    write_csv(watch_dir / "alipay.csv", ALIPAY_ROWS)
    calls = {"n": 0}
    real_import = import_watch_service.import_service.import_local_file

    def broken_import(*a, **kw):
        calls["n"] += 1
        raise RuntimeError("解析崩溃")

    import_watch_service.import_service.import_local_file = broken_import
    try:
        affected, message = _scan()
        assert affected == 0 and "失败 1" in message
        record = ImportedFileDAO.get_by_path(str(watch_dir / "alipay.csv"))
        assert record["status"] == "failed"
        assert "解析崩溃" in record["message"]

        affected, _ = _scan()  # 同内容：不重试
        assert affected == 0
        assert calls["n"] == 1
    finally:
        import_watch_service.import_service.import_local_file = real_import

    rows = [r.copy() for r in ALIPAY_ROWS]
    rows[2] = rows[2].copy()
    rows[2][9] = "NAS-ALI-0009"
    write_csv(watch_dir / "alipay.csv", rows)  # 内容变化 → 重新处理，恢复后成功
    affected, message = _scan()
    assert affected == 1 and calls["n"] == 1


def test_oversized_file_ignored(db, watch_dir, monkeypatch):
    """超过单文件大小上限的文件不进入处理（沿用 10MB 安全边界）"""
    from app.config import NAS_MAX_FILE_SIZE

    big = watch_dir / "big.csv"
    big.write_text("x" * (NAS_MAX_FILE_SIZE + 1), encoding="utf-8")
    affected, message = _scan()
    assert affected == 0
    assert ImportedFileDAO.get_by_path(str(big)) is None


def test_nested_dir_scanned(db, watch_dir):
    """子目录中的账单同样纳入扫描（目录树遍历）"""
    sub = watch_dir / "2024"
    sub.mkdir()
    write_csv(sub / "jd.csv", JD_ROWS)
    affected, _ = _scan()
    assert affected >= 1
    assert ImportedFileDAO.get_by_path(str(sub / "jd.csv"))["status"] == "ok"
