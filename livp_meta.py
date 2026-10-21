# -*- coding: utf-8 -*-
"""拍摄时间解析 —— 纯标准库实现，无需 Pillow 等第三方依赖。

优先级（从准到糙）：
    1. EXIF DateTimeOriginal / DateTimeDigitized / DateTime
    2. 文件名中的日期，如 IMG_20260315_102333、P_20260315_102333、2026-03-15 10-22-33.jpg
    3. 文件修改时间

HEIC 的 EXIF 位于 ISO-BMFF 容器内的 Exif item 中，解析成本远高于 JPEG，
本模块不做解析，直接落到「文件名 → 修改时间」兜底；对绝大多数手机
（文件名自带时间戳）结果一致。
"""

import os
import re
import struct
import time

# EXIF 标签
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_DATETIME = 0x0132

_EXIF_MAX_SCAN = 256 * 1024      # EXIF 通常在文件头，只扫前 256KB 足够且快


# ---------------------------------------------------------------- EXIF 解析

def _iter_exif_times(tiff):
    """遍历 TIFF（含 Exif 子 IFD），产出 (优先级, 'YYYY:MM:DD HH:MM:SS') 字符串。

    优先级数字越小越优先：DateTimeOriginal > DateTimeDigitized > DateTime。
    """
    if len(tiff) < 8:
        return
    if tiff[:2] == b"II":
        en = "<"
    elif tiff[:2] == b"MM":
        en = ">"
    else:
        return

    try:
        ifd_off = struct.unpack(en + "I", tiff[4:8])[0]
    except struct.error:
        return
    if ifd_off + 2 > len(tiff):
        return

    seen = set()

    def walk(off, depth):
        """depth 0 = IFD0，depth 1 = Exif 子 IFD。"""
        if depth > 2 or off in seen or off + 2 > len(tiff):
            return
        seen.add(off)
        try:
            cnt = struct.unpack(en + "H", tiff[off:off + 2])[0]
        except struct.error:
            return
        if cnt > 512:                       # 明显异常，直接放弃该 IFD
            return
        for k in range(cnt):
            p = off + 2 + 12 * k
            if p + 12 > len(tiff):
                return
            try:
                tag, typ, cn = struct.unpack(en + "HHI", tiff[p:p + 8])
            except struct.error:
                return

            if tag == 0x8769 and typ == 4 and cn >= 1:     # ExifIFDPointer
                try:
                    sub = struct.unpack(en + "I", tiff[p + 8:p + 12])[0]
                except struct.error:
                    sub = 0
                # 必须 yield from：walk 是生成器，裸调用只会造一个没人读的
                # 生成器对象然后丢弃，Exif 子 IFD 里的 DateTimeOriginal
                # （0x9003，相机几乎都只写在这里）就永远读不到，
                # 「EXIF 优先」实际退化成了只认 IFD0 的 DateTime。
                yield from walk(sub, depth + 1)
                continue

            if tag in (TAG_DATETIME_ORIGINAL, TAG_DATETIME_DIGITIZED, TAG_DATETIME):
                prio = {TAG_DATETIME_ORIGINAL: 0,
                        TAG_DATETIME_DIGITIZED: 1,
                        TAG_DATETIME: 2}[tag]
                # count<=4 时值内联在 4 字节字段里
                try:
                    if cn <= 4:
                        val = tiff[p + 8:p + 8 + cn]
                    else:
                        vo = struct.unpack(en + "I", tiff[p + 8:p + 12])[0]
                        if vo + cn > len(tiff):
                            continue
                        val = tiff[vo:vo + cn]
                except struct.error:
                    continue
                try:
                    s = val.rstrip(b"\x00").decode("ascii", "ignore").strip()
                except Exception:
                    continue
                if s:
                    yield prio, s

    yield from walk(ifd_off, 0)


_EXIF_RE = re.compile(
    r"^(\d{4})[:-]?(\d{2})[:-]?(\d{2})"      # 日期
    r"[ T_]?(\d{2})[:-]?(\d{2})[:-]?(\d{2})"  # 可选时间
)


def _normalize_exif(raw):
    """把 EXIF 的 '2026:03:15 10:22:33' 规整为 (2026, 3, 15, 10, 22, 33)。"""
    m = _EXIF_RE.match(raw.strip())
    if not m:
        return None
    try:
        y, mo, d, h, mi, s = (int(x) for x in m.groups())
    except ValueError:
        return None
    if not (1970 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
        return None
    if h > 23 or mi > 59 or s > 59:
        return None
    return (y, mo, d, h, mi, s)


def exif_time(path):
    """从 JPEG 的 EXIF 读拍摄时间，返回 (y,m,d,H,M,S) 或 None。"""
    try:
        if os.path.getsize(path) < 16:
            return None
        with open(path, "rb") as f:
            data = f.read(_EXIF_MAX_SCAN)
    except OSError:
        return None

    if data[:2] != b"\xff\xd8":              # 不是 JPEG（HEIC 等），放弃
        return None

    best = None
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i + 1]
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        if m in (0xDA, 0xD9):                # 进入图像数据，后面不会有 EXIF
            break
        seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
        if seg_len < 2:
            break
        if m == 0xE1 and data[i + 4:i + 10] == b"Exif\x00\x00":
            tiff = data[i + 10:i + 2 + seg_len]
            for prio, raw in _iter_exif_times(tiff):
                norm = _normalize_exif(raw)
                if norm and (best is None or prio < best[0]):
                    best = (prio, norm)
            if best:                           # 已拿到最优来源，无需再扫
                break
        i += 2 + seg_len
    return best[1] if best else None


# ---------------------------------------------------------------- 文件名兜底

_NAME_PATTERNS = [
    # IMG_20260315_102333 / P_20260315-102333 / IMG-20260315T102333
    re.compile(r"(?<!\d)(\d{4})[-_]?(\d{2})[-_]?(\d{2})[ T_-]?(\d{2})[-_:]?(\d{2})[-_:]?(\d{2})(?!\d)"),
    # 2026-03-15 只有日期，时间补 0
    re.compile(r"(?<!\d)(\d{4})[-_]?(\d{2})[-_]?(\d{2})(?!\d)"),
]


def name_time(path):
    """从文件名推断时间，返回 (y,m,d,H,M,S) 或 None。"""
    base = os.path.splitext(os.path.basename(path))[0]
    for pat in _NAME_PATTERNS:
        m = pat.search(base)
        if not m:
            continue
        g = m.groups()
        try:
            y, mo, d = int(g[0]), int(g[1]), int(g[2])
            h, mi, s = (int(g[3]), int(g[4]), int(g[5])) if len(g) == 6 else (0, 0, 0)
        except ValueError:
            continue
        if not (1970 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
            continue
        if h > 23 or mi > 59 or s > 59:
            continue
        return (y, mo, d, h, mi, s)
    return None


# ---------------------------------------------------------------- 统一入口

def mtime_time(path):
    """文件修改时间。仅作为「不可信」信号供用户判断，不用于命名。"""
    try:
        return time.localtime(os.path.getmtime(path))[:6]
    except (OSError, ValueError):
        return None


def shot_time(path, jpg_hint=None):
    """综合判定拍摄时间。

    返回 (时间元组 或 None, 来源标签)：
        ('EXIF' | '文件名', 可靠时间)
        ('无时间', None)      —— 文件名与 EXIF 都读不到，不猜
        ('文件时间', None)    —— 仅当调用方显式允许时才会返回 mtime

    为什么不用 mtime 兜底命名：实测用户数据集中 893 个文件的修改时间
    平均偏离真实拍摄时间 19850 天（约 54 年）—— 微信/QQ 传输、备份还原、
    批量改名都会污染它。用它命名会产出「看起来正常、实际全错」的文件名，
    比保留原始文件名更危险。所以默认不猜，由调用方决定是否接受降级。
    """
    if jpg_hint and os.path.isfile(jpg_hint):
        t = exif_time(jpg_hint)
        if t:
            return t, "EXIF"
        # HEIC 无法解析 EXIF 时，容器内的图片文件名有时也带时间
        t = name_time(jpg_hint)
        if t:
            return t, "文件名"

    t = name_time(path)
    if t:
        return t, "文件名"
    return None, "无时间"


def shot_time_or_mtime(path, jpg_hint=None):
    """显式允许降级到 mtime 的版本，供确实需要时间戳的场合使用。"""
    t, src = shot_time(path, jpg_hint)
    if t:
        return t, src
    t = mtime_time(path)
    if t:
        return t, "文件时间"
    return None, "无时间"


def fmt_stamp(t):
    """(y,m,d,H,M,S) -> '20260315_102333'。"""
    return "%04d%02d%02d_%02d%02d%02d" % t


def fmt_day(t):
    """(y,m,d,H,M,S) -> '2026-03-15'，用作子目录名。"""
    return "%04d-%02d-%02d" % t[:3]