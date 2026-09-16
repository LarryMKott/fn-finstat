"""修正 fnpack 在 Windows 上打包丢失的可执行权限位

fnpack 的 Windows 版本打包时 cmd/ 生命周期脚本权限位为 0666，
安装到 fnOS 后平台可能无法直接执行。本脚本重写 FPK（gzip tar），
将 cmd/ 下全部文件权限位设为 0755，其余文件 0644、目录 0755。

用法：
    python scripts/fix_fpk_perm.py <path/to/app.fpk>
"""

import sys
import tarfile
import tempfile
from pathlib import Path

DIR_MODE = 0o755
FILE_MODE = 0o644
CMD_MODE = 0o755


def fix(fpk_path: Path) -> None:
    # CLI 显式指定的目标文件：规范化并拒绝 .. 穿越分量，仅接受 .fpk 后缀
    resolved = fpk_path.resolve()
    if ".." in fpk_path.parts or resolved.suffix != ".fpk":
        raise SystemExit(f"仅接受明确的 .fpk 文件路径：{fpk_path}")
    with tarfile.open(resolved, "r:gz") as src:
        members = src.getmembers()
        # 临时文件必须与目标同目录，避免 Windows 跨盘符无法 replace
        with tempfile.NamedTemporaryFile(
            suffix=".fpk", dir=resolved.parent, delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
        with tarfile.open(tmp_path, "w:gz") as dst:
            for m in members:
                if m.isdir():
                    m.mode = DIR_MODE
                elif m.isfile() and m.name.startswith("cmd/"):
                    m.mode = CMD_MODE
                else:
                    m.mode = FILE_MODE
                dst.addfile(m, src.extractfile(m))
    tmp_path.replace(resolved)
    print(f"fixed: {resolved} ({len(members)} entries, cmd/* -> {oct(CMD_MODE)})")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    fix(Path(sys.argv[1]))
