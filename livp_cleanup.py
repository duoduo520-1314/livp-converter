# -*- coding: utf-8 -*-
"""转换完成后清理源 .livp —— 通过 Windows 回收站，可恢复。

安全设计（这个功能涉及不可逆的数据后果，因此格外保守）：

1. **绝不用 os.remove / rm**。一律走 shell32.SHFileOperationW 的
   FO_DELETE + FOF_ALLOWUNDO，文件进入回收站而非被抹除。
2. **两阶段**：全部转换成功后才开始清理。中途停止或失败的源文件一律保留。
3. **逐个校验产物**：只有确认 JPG（及 MP4/GIF，若启用）落盘且非空，才删对应源文件。
4. **默认关闭**，勾选时必须经过风险提示与二次确认。
5. **分批提交**：单次 SHFileOperation 的路径缓冲区有上限，893 个文件需分批。
6. 回收站空间不足时系统会返回错误码，此时保留源文件并如实报告，绝不静默失败。
"""

import ctypes
import ctypes.wintypes as wt
import os

# SHFileOperationW 函数常量
FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040          # 关键：进回收站而非彻底删除
FOF_NOERRORUI = 0x0400

# 单批路径数上限。SHFileOperationW 的 pFrom 是以双 \0 结尾的多字符串缓冲区，
# 缓冲区过长时调用会失败而非截断，因此按数量分批更稳。
BATCH_SIZE = 40

# 返回码含义（节选，便于给用户报可读的错误）
_RC = {
    0x00: "成功",
    0x71: "源文件与目标文件相同",
    0x72: "路径过长",
    0x74: "拒绝访问，文件可能只读或被占用",
    0x75: "同名目标已存在",
    0x76: "跨磁盘移动，回收站无法存放",
    0x78: "被用户取消",
    0x7C: "路径无效",
    0x7E: "回收站空间不足",
    0x80: "指定了同名的多个源文件",
}


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("wFunc", wt.UINT),
        ("pFrom", wt.LPCWSTR),
        ("pTo", wt.LPCWSTR),
        ("fFlags", wt.UINT),
        ("fAnyOperationsAborted", wt.BOOL),
        ("hNameMappings", wt.LPVOID),
        ("lpszProgressTitle", wt.LPCWSTR),
    ]


def _available():
    try:
        return hasattr(ctypes.windll.shell32, "SHFileOperationW")
    except Exception:
        return False


def recycle(paths):
    """把 paths 送进回收站。

    返回 (成功数, 失败数, 错误描述列表)。文件不存在视为已清理（幂等）。
    """
    targets = []
    lost = 0
    for p in paths or []:
        ap = os.path.abspath(p)
        if os.path.exists(ap):
            targets.append(ap)
        else:
            lost += 1

    if not targets:
        return lost, 0, []

    if not _available():
        return 0, len(targets), ["当前系统不支持回收站操作，源文件已全部保留"]

    ok = lost
    errs = []
    fail = 0
    flags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT

    for i in range(0, len(targets), BATCH_SIZE):
        batch = targets[i:i + BATCH_SIZE]
        op = _SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        # pFrom 必须是「每个路径以 \0 结尾 + 末尾额外一个 \0」的双零结尾字符串
        op.pFrom = "".join(p + "\0" for p in batch) + "\0"
        op.pTo = None
        op.fFlags = flags
        try:
            rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        except Exception as e:
            fail += len(batch)
            errs.append("回收站调用失败：%s" % e)
            continue

        if rc == 0 and not op.fAnyOperationsAborted:
            ok += len(batch)
            continue

        # 整批失败时逐个重试，定位真正有问题的文件（可能是被占用或超长路径）
        if len(batch) == 1:
            fail += 1
            errs.append("%s：%s" % (os.path.basename(batch[0]),
                                     _RC.get(rc, "错误码 0x%02X" % rc)))
            continue
        sub_ok, sub_fail, sub_errs = recycle(batch)
        ok += sub_ok
        fail += sub_fail
        errs.extend(sub_errs)

    return ok, fail, errs


def verify_outputs(jpg, extra=()):
    """确认产物齐全且非空。extra 为额外要求存在的文件。"""
    if not jpg or not os.path.isfile(jpg) or os.path.getsize(jpg) <= 0:
        return False
    for p in extra:
        if p and (not os.path.isfile(p) or os.path.getsize(p) <= 0):
            return False
    return True


def total_size(paths):
    """待清理文件占用空间（字节），用于风险提示。"""
    n = 0
    for p in paths or []:
        try:
            n += os.path.getsize(p)
        except OSError:
            pass
    return n


def fmt_size(n):
    """字节数转易读文本。"""
    if n <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
    return "%.1f TB" % n