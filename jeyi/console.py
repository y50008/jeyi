"""console.py — Windows 控制台中文输出兜底

Windows 的 cmd/PowerShell 默认代码页是 GBK/CP1252，`print("导出 3 节点")`
会直接抛 UnicodeEncodeError 把进程干掉（GitHub Actions 的 windows-latest
就是 CP1252，这就是 CI 只在 Windows 上挂的原因，不是测试失败）。

用法：在会打印中文的入口顶部加两行：

    from jeyi.console import ensure_utf8
    ensure_utf8()

刻意不在 `import jeyi` 时就改全局 stdout——库不该动调用方的 IO。
"""
from __future__ import annotations
import sys


def ensure_utf8() -> bool:
    """把 stdout/stderr 切到 UTF-8；已经是 UTF-8 就什么都不做。返回是否改动过。"""
    changed = False
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is None:
                continue
            enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
            if enc in ("utf8", ""):
                continue
            stream.reconfigure(encoding="utf-8", errors="replace")
            changed = True
        except (AttributeError, ValueError, OSError):
            # 被重定向到管道/文件且不支持 reconfigure：忽略即可，errors=replace 已兜底
            pass
    return changed
