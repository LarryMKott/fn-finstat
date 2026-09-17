"""为打包产物生成 MD5 校验文件（GNU coreutils 格式，可直接 `md5sum -c` 校验）

为什么单独成文件，而不是塞进 releaseNode.txt：
  - `releaseNode.txt` 是 Release 描述用的日志，内容随提交历史变化，且允许生成
    失败后降级；校验文件必须格式稳定、缺一个产物就得失败——两者的容错策略相反
  - 校验文件要能被脚本逐行解析（`md5sum -c`），不能混入标题、表格等 Markdown

输出格式（每行 `<32 位小写 md5><两个空格><文件名>`，按文件名排序）：

    d41d8cd98f00b204e9800998ecf8427e  fn-finstat-latest.fpk
    0cc175b9c0f1b6a831c399e269772661  fn-finstat-v0.7.1.fpk

在产物所在目录执行 `md5sum -c MD5SUMS.txt` 即可逐个校验。

> MD5 只用于**传输完整性**校验（下载是否残缺），不是防篡改手段——需要防篡改时
> 用 Release 日志里的 SHA-256。

只用标准库：CI（Linux）与本地（Windows：Git Bash 的 md5sum 在 PATH 损坏时不可用）
都靠 Python 出结果，不依赖 shell 工具。

用法:
  python scripts/gen_checksums.py -o MD5SUMS.txt fn-finstat-latest.fpk fn-finstat-v0.7.1.fpk
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

# 分块读取：fpk 只有几百 KB，但按块读可避免将来产物变大时整包进内存
CHUNK_SIZE = 1024 * 1024


def md5_of(path: Path) -> str:
    """计算文件 MD5（小写十六进制）"""
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(block)
    return digest.hexdigest()


def build_lines(paths: list[Path]) -> list[str]:
    """生成校验行；任一产物缺失即抛 FileNotFoundError（不许静默少一行）

    缺产物说明构建链路本身出了问题，此时生成一份"部分校验"的文件比不生成更危险：
    用户拿它校验会得到"文件缺失"，却以为是下载坏了。
    """
    lines: list[str] = []
    for path in sorted(paths, key=lambda item: item.name):
        if not path.is_file():
            raise FileNotFoundError(f"产物不存在，无法生成校验值：{path}")
        lines.append(f"{md5_of(path)}  {path.name}")
    return lines


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="为打包产物生成 MD5 校验文件")
    parser.add_argument("files", nargs="+", help="要校验的产物路径")
    parser.add_argument(
        "-o",
        "--output",
        default="MD5SUMS.txt",
        help="输出文件（默认 MD5SUMS.txt）",
    )
    args = parser.parse_args(argv)

    try:
        lines = build_lines([Path(name) for name in args.files])
    except FileNotFoundError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    out = Path(args.output)
    # 固定 LF 换行：Windows 下写出的 CRLF 会让 `md5sum -c` 把 \r 当作文件名的一部分，
    # 于是每个文件都报 "No such file or directory"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    for line in lines:
        print(line)
    print(
        f"==> 已写入 {out}（{len(lines)} 个产物，可用 md5sum -c {out.name} 校验）",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
