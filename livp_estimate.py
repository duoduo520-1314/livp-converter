# -*- coding: utf-8 -*-
"""转换耗时与产物体积的估算。

为什么需要：点「开始转换」之前，用户对代价一无所知。而耗时和体积会随选项
剧烈变化 —— 勾 GIF、关掉跳过已转换、把画质拉到 8，都能成倍放大。
估算能让用户在开始前就判断「这批要花 40 分钟、占 5 GB，能不能接受」。

数据来源：893 个真实 .livp 文件（17 个相册）的实测均值，单文件实测：
  · JPG + MP4 转换      约 1.24 秒/个
  · GIF 额外            约 2.77 秒/个、约 6.0 MB/个
  · JPG + MP4 输出       约 64 MB / 893 个
  · 原始 .livp 副本      约 5.78 GB / 893 个
估算只是量级参考，不是承诺；界面上会如实标注为「估算」。

单独成模块而非写进 GUI，便于测试，也避免经验值散落在各个回调里。
纯标准库实现。
"""

# ---- 实测经验值（单文件）----
SEC_PER_FILE_JPG_MP4 = 1.24# JPG + MP4 转换耗时（秒）
SEC_PER_FILE_GIF = 2.77        # GIF 额外耗时（秒）
MB_PER_FILE_GIF = 6.0          # GIF 产物体积（MB）
MB_TOTAL_JPG_MP4_893 = 64.0    # 893 张的 JPG + MP4 总体积（MB）
MB_PER_LIVP_SOURCE = 5.78 * 1024 / 893   # 单个 .livp 平均体积（MB）
REF_COUNT = 893                # 经验值的基准文件数

# 画质对体积的影响（jpeg -q:v 越大压得越狠）。以 quality=2 为基准 1.0。
QUALITY_SIZE_FACTOR = {
    1: 1.35, 2: 1.00, 3: 0.85, 4: 0.72, 5: 0.62,
    6: 0.55, 7: 0.50, 8: 0.45,
}


def estimate(count, opts, done_count=0):
    """估算耗时（秒）与产物体积（MB）。

    count     待处理文件总数
    opts      与 _collect_opts() 同结构的字典
    done_count 已在任务记录中标记完成的数量，会被跳过，不计入本次开销

    返回 dict：seconds / out_mb / per_file_sec / skipped
    """
    todo = max(0, int(count) - int(done_count))
    if todo == 0:
        return {"seconds": 0.0, "out_mb": 0.0,
                "per_file_sec": 0.0, "skipped": int(done_count)}

    per = SEC_PER_FILE_JPG_MP4
    size = MB_TOTAL_JPG_MP4_893 * todo / REF_COUNT

    if opts.get("quality"):
        size *= QUALITY_SIZE_FACTOR.get(int(opts["quality"]), 1.0)
    if opts.get("gif"):
        per += SEC_PER_FILE_GIF
        size += MB_PER_FILE_GIF * todo
    if opts.get("keep_heic"):
        # 额外复制一份 HEIC + MOV，体积与源文件相当
        size += MB_PER_LIVP_SOURCE * todo

    return {"seconds": per * todo, "out_mb": size,
            "per_file_sec": per, "skipped": int(done_count)}


def format_duration(seconds):
    """把秒数说成人话。不足 1 分钟用秒，不足 1 小时用分钟。"""
    s = float(seconds)
    if s < 60:
        return "约 %d 秒" % max(1, round(s))
    if s < 3600:
        m = s / 60.0
        return "约 %d 分钟" % round(m) if m < 10 else "约 %.0f 分钟" % m
    h = s / 3600.0
    if h < 10:
        return "约 %.1f 小时" % h
    return "约 %.0f 小时" % h


def format_size(mb):
    """把 MB 说成人话。"""
    m = float(mb)
    if m < 1:
        return "不到 1 MB"
    if m < 1024:
        return "约 %.0f MB" % m
    return "约 %.1f GB" % (m / 1024.0)


def describe(count, opts, done_count=0):
    """生成一行可直接显示的预估文本。

    无文件时返回空串；全部已完成时提示无需转换。
    """
    if count <= 0:
        return ""
    est = estimate(count, opts, done_count)
    if est["seconds"] <= 0:
        return "全部 %d 个文件上次已完成，无需重复转换" % count

    text = "预计耗时 %s，产物体积 %s" % (format_duration(est["seconds"]),
                                       format_size(est["out_mb"]))
    if est["skipped"]:
        text += "（另有 %d 个已完成，将跳过）" % est["skipped"]
    if opts.get("gif"):
        text += "　⚠ 含 GIF，耗时与体积明显增加"
    return text