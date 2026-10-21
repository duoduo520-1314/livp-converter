# -*- coding: utf-8 -*-
"""
LIVP 转换工具 —— 图形界面版
把 .livp（实况照片）批量拆分为 JPG 静态图 + MP4 视频。
"""

import os
import sys
import zipfile
import shutil
import subprocess
import threading
import queue
import time
import tempfile
import fnmatch
import re
import traceback

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText

# 同目录模块（打包成 exe 后由 pyinstaller 一并收集）
try:
    import livp_meta
    import livp_state
    import livp_contact
    import livp_cleanup
    import livp_estimate
except ImportError:      # 源码直接双击运行时也可能走到这里
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import livp_meta
    import livp_state
    import livp_contact
    import livp_cleanup
    import livp_estimate


# ---------------------------------------------------------------- ffmpeg 发现

def find_ffmpeg():
    """定位 ffmpeg：优先 exe 同级目录，其次系统 PATH。"""
    base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False)
                                                    else __file__))
    for folder in (base, os.path.join(base, "ffmpeg"), base):
        cand = os.path.join(folder, "ffmpeg.exe")
        if os.path.isfile(cand):
            return cand
    path = shutil.which("ffmpeg")
    if path:
        return path
    return None


FFMPEG = find_ffmpeg()

# 打包为 --windowed 后主进程本身没有控制台，但启动子进程时 Windows 仍会
# 默认新建一个控制台窗口，表现为转换过程中反复闪出黑色命令行窗口。
# CREATE_NO_WINDOW 明确要求子进程不分配控制台，输出照常走管道捕获。
# 仅 Windows 支持；其他平台传0 即可。
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# ---------------------------------------------------------------- 核心转换

class Converter:
    """单个 .livp 的转换逻辑。"""

    @staticmethod
    def probe(sf):
        """打开 livp，返回 [(内部名, 大小), ...]；失败抛异常。"""
        with zipfile.ZipFile(sf) as z:
            return [(i.filename, i.file_size) for i in z.infolist()]

    @staticmethod
    def extract(sf, workdir):
        """解压到临时目录，返回 (图片路径, 视频路径或None)。"""
        with zipfile.ZipFile(sf) as z:
            z.extractall(workdir)
        img = vid = None
        for root, _dirs, files in os.walk(workdir):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                # .heic.heic / .jpg.jpeg 这类双后缀，取最后一个后缀判断
                if ext in (".heic", ".heif", ".jpg", ".jpeg", ".png"):
                    p = os.path.join(root, fn)
                    if img is None or os.path.getsize(p) > os.path.getsize(img):
                        img = p
                elif ext in (".mov", ".mp4", ".m4v"):
                    vid = os.path.join(root, fn)
        return img, vid

    @staticmethod
    def run(cmd):
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           creationflags=NO_WINDOW)
        return p.returncode, (p.stdout or b"").decode("utf-8", "ignore")

    @staticmethod
    def to_jpg(img_path, out_path, quality=2):
        """HEIC/JPEG 主图 -> JPG。

        关键点：不能加 -map 0:v:0。HEIC 内部第一个流常是 512x512 缩略图，
        显式 map 会导出缩略图。交给 ffmpeg 自动选流才能拿到全尺寸主图。
        """
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
               "-i", img_path,
               "-frames:v", "1",
               "-q:v", str(quality),
               "-update", "1",
               out_path]
        rc, log = Converter.run(cmd)
        if rc != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("图片转换失败: " + log.strip()[:400])

    @staticmethod
    def to_mp4(mov_path, out_path, crf=20):
        """MOV(HEVC) -> MP4(H.264)，兼容性最好，任何设备都能播。"""
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
               "-i", mov_path,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf),
               "-pix_fmt", "yuv420p",
               "-movflags", "+faststart",
               "-c:a", "aac", "-b:a", "128k",
               out_path]
        rc, log = Converter.run(cmd)
        if rc != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("视频转换失败: " + log.strip()[:400])

    @staticmethod
    def to_gif(mov_path, out_path, fps=12, width=480):
        """MOV -> GIF，方便直接发微信/社交平台。

        注意：GIF 体积大、生成慢（约 2.8s/个，且单文件常达数 MB），
        默认关闭。需要时应主动提示用户体积代价。
        """
        vf = ("fps=%d,scale=%d:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse"
              % (fps, width))
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
               "-i", mov_path, "-vf", vf, "-loop", "0", out_path]
        rc, log = Converter.run(cmd)
        if rc != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("GIF 生成失败: " + log.strip()[:400])


# ---------------------------------------------------------------- 工作线程

class Worker(threading.Thread):
    """后台线程池：逐个转换，通过 queue 与界面通信。

    每完成/跳过一个文件就回报 ('progress', src)，界面据此增量落盘状态，
    实现「关掉程序再打开能接着上次继续」。
    """

    def __init__(self, jobs, out_root, opts, q, used=()):
        super().__init__(daemon=True)
        self.jobs = jobs
        self.out_root = out_root
        self.opts = opts
        self.q = q
        self.stop_flag = threading.Event()
        # 本次运行已占用的输出文件名，用于同一秒连拍时防覆盖
        self.used = set(used or ())
        # (源文件, [产物...])，供清理阶段逐一校验后才允许删源
        self.produced = []

    def stop(self):
        self.stop_flag.set()

    def _unique(self, path):
        """同一时间戳可能对应连拍的多张，追加 _2/_3 保证不互相覆盖。

        无论最终用哪个名字，都会把「标准名」登记进 used —— 后续同标准名的
        文件若再走skip 检查就能识别出「这是本轮刚产出的」，不会漏转换。
        """
        std = path
        if path not in self.used and not os.path.exists(path):
            self.used.add(path)
            return path
        base, ext = os.path.splitext(path)
        for i in range(2, 10000):
            cand = "%s_%d%s" % (base, i, ext)
            if cand not in self.used and not os.path.exists(cand):
                self.used.add(cand)
                self.used.add(std)
                return cand
        return path

    def run(self):
        total = len(self.jobs)
        self.q.put(("total", total))
        ok = fail = skip = 0
        no_time = 0

        for idx, (src, rel) in enumerate(self.jobs, 1):
            if self.stop_flag.is_set():
                break
            self.q.put(("item", idx, src))

            try:
                # 拍摄时间要在建目录、算文件名之前拿到
                t, tsrc = self._resolve_time(src)
                if t is None:
                    # 读不到可靠时间就不猜：保留原文件名，比用被污染的
                    # 文件修改时间生成「看起来对、实际错」的名字安全得多
                    no_time += 1
                rel_dir = rel or ""
                if self.opts.get("by_date") and t:
                    rel_dir = os.path.join(rel_dir, livp_meta.fmt_day(t)) \
                        if rel_dir else livp_meta.fmt_day(t)

                dst_dir = os.path.join(self.out_root, rel_dir) if rel_dir else self.out_root
                if self.opts.get("by_time") and t:
                    stem = livp_meta.fmt_stamp(t)
                else:
                    stem = os.path.splitext(os.path.basename(src))[0]

                os.makedirs(dst_dir, exist_ok=True)
                # 先算「标准名」：skip 检查必须基于它，否则续传时因 _unique 改名而永远命不中
                base_jpg = os.path.join(dst_dir, stem + ".jpg")
                base_mp4 = os.path.join(dst_dir, stem + ".mp4")
                base_gif = os.path.join(dst_dir, stem + ".gif")

                # 「跳过已转换」：主产物都存在、且不是本次运行刚产出的同名文件时才跳过。
                # 后半条很关键——同一秒连拍的多张标准名相同，若只判文件存在会漏转换。
                if self.opts.get("skip_existing") and base_jpg not in self.used:
                    have = os.path.isfile(base_jpg) and os.path.getsize(base_jpg) > 0
                    if self.opts.get("mp4", True):
                        have = have and os.path.isfile(base_mp4) \
                            and os.path.getsize(base_mp4) > 0
                    if self.opts.get("gif", False):
                        have = have and os.path.isfile(base_gif) \
                            and os.path.getsize(base_gif) > 0
                    if have:
                        skip += 1
                        self.q.put(("progress", src))
                        self.q.put(("done", idx, src, "已存在，跳过"))
                        continue

                # 确需产出时才占用名字：同一秒连拍得到 _2 / _3，互不覆盖
                jpg = self._unique(base_jpg)
                mp4 = os.path.join(dst_dir, os.path.splitext(jpg)[0] + ".mp4")
                gif = os.path.join(dst_dir, os.path.splitext(jpg)[0] + ".gif")

                done = []
                work = tempfile.mkdtemp(prefix="livp_")
                try:
                    img, vid = Converter.extract(src, work)

                    if img is None:
                        raise RuntimeError("容器内未找到图片")

                    # 拿到容器内真实图片后，用 EXIF 再确认一次拍摄时间
                    if self.opts.get("by_time") and tsrc != "EXIF":
                        t2, tsrc2 = livp_meta.shot_time(src, img)
                        if t2 and tsrc2 == "EXIF":
                            t, tsrc = t2, tsrc2

                    if self.opts.get("keep_heic"):
                        src_ext = os.path.splitext(img)[1].lower()
                        keep = os.path.join(dst_dir, os.path.splitext(jpg)[0] + src_ext)
                        if not os.path.exists(keep):
                            shutil.copy2(img, keep)
                        done.append("原图")

                    Converter.to_jpg(img, jpg, self.opts.get("quality", 2))
                    done.append("JPG")

                    if vid:
                        if self.opts.get("mp4", True):
                            Converter.to_mp4(vid, mp4, self.opts.get("crf", 20))
                            done.append("MP4")
                        if self.opts.get("gif", False):
                            Converter.to_gif(vid, gif, self.opts.get("gif_fps", 12))
                            done.append("GIF")
                        if self.opts.get("keep_mov", False):
                            keep = os.path.join(dst_dir, os.path.splitext(jpg)[0] + ".mov")
                            if not os.path.exists(keep):
                                shutil.copy2(vid, keep)
                            done.append("MOV")
                    if self.stop_flag.is_set():
                        break
                finally:
                    shutil.rmtree(work, ignore_errors=True)

                ok += 1
                # 登记产物供清理阶段校验：只有确认落盘才允许删源文件。
                # 注意「已存在跳过」的分支走continue，不登记到 produced——
                # 续传时被跳过的旧文件同样安全，不会因本轮开启清理而被误删。
                self.produced.append((src, [jpg] + ([mp4] if vid and
                                                    self.opts.get("mp4", True) else [])))
                label = " + ".join(done)
                if t is None:
                    label += "  [无拍摄时间，沿用原名]"
                else:
                    label += "  [%s]" % tsrc
                self.q.put(("progress", src))
                self.q.put(("done", idx, src, label))

            except Exception as e:
                fail += 1
                self.q.put(("fail", idx, src, str(e)))

        stopped = self.stop_flag.is_set()
        cleanup = (0, 0, [])
        if self.opts.get("cleanup") and not stopped and fail == 0:
            cleanup = self._cleanup_sources()
        self.q.put(("finish", ok, fail, skip, stopped, no_time, cleanup))

    def _cleanup_sources(self):
        """两阶段清理：逐个校验产物落盘后，把源 .livp 送进回收站。

        仅在无失败、未手动停止时调用。任一产物缺失则跳过该文件并保留源文件。
        """
        deletable = []
        kept = []
        for src, outs in self.produced:
            if livp_cleanup.verify_outputs(outs[0], extra=outs[1:]):
                deletable.append(src)
            else:
                kept.append(src)
        if kept:
            self.q.put(("cleanup_keep", kept))
        return livp_cleanup.recycle(deletable)

    def _resolve_time(self, src):
        """外层（不解压）先估一次时间，避免为不需要时间的场景付出解压代价。"""
        t, tsrc = livp_meta.shot_time(src)
        return t, tsrc


# ---------------------------------------------------------------- 界面

class SheetWorker(threading.Thread):
    """后台生成联系表。

    首次为893 张生成缩略图实测约 189 秒，此前这段跑在主线程上，
    窗口会整整冻结三分钟并被 Windows 判定「未响应」。必须放后台。

    通过 queue 回报 ('sheet', 事件, ...) ；stop_flag 允许中途放弃。
    """

    def __init__(self, out_root, entries, ffmpeg, q):
        super().__init__(daemon=True)
        self.out_root = out_root
        self.entries = entries
        self.ffmpeg = ffmpeg
        self.q = q
        self.stop_flag = threading.Event()

    def stop(self):
        self.stop_flag.set()

    def run(self):
        total, miss = livp_contact.count_missing(self.entries, self.out_root)
        self.q.put(("sheet_total", total, miss))
        if self.stop_flag.is_set():
            self.q.put(("sheet_done", None, True, None, None))
            return

        stats = {}

        def on_progress(done, _total, name):
            # 进度回报要节流：893 张逐张 put 会把队列塞满，
            # 每 8 张或最后一张才报一次，既跟得上又不会拖慢界面
            if done % 8 == 0:
                self.q.put(("sheet_progress", done, name))

        try:
            path = livp_contact.build(
                self.out_root, self.entries, self.ffmpeg,
                progress=on_progress, stop_flag=self.stop_flag, stats=stats)
        except Exception as e:
            # 消息格式必须与成功路径一致（5 元组），否则界面会把
            # stats 当成错误信息显示成「生成失败」
            self.q.put(("sheet_done", None, False, str(e), None))
            return
        stopped = self.stop_flag.is_set()
        self.q.put(("sheet_done", path, stopped, None, stats))


class App(tk.Tk):
    BG = "#f5f6f8"
    CARD = "#ffffff"
    FG = "#1f2328"
    MUTED = "#6b7280"
    ACCENT = "#2563eb"
    BORDER = "#d9dde3"

    def __init__(self):
        super().__init__()
        self.title("LIVP 实况照片转换器")
        self.geometry("940x700")
        self.minsize(880, 640)
        self.configure(bg=self.BG)

        self.files = []           # [(绝对路径, 相对目录)]
        self.q = queue.Queue()
        self.worker = None
        self.sheet_worker = None

        self.var_src = tk.StringVar()
        self.var_out = tk.StringVar()
        self.var_preserve = tk.BooleanVar(value=True)
        self.var_mp4 = tk.BooleanVar(value=True)
        self.var_gif = tk.BooleanVar(value=False)
        self.var_keep = tk.BooleanVar(value=False)
        self.var_skip = tk.BooleanVar(value=True)
        self.var_by_time = tk.BooleanVar(value=False)
        # 默认关闭：多数相册目录（如「2024云南2日游」）本身已是有意义的组织方式，
        # 默认按日期分层反而会把它们打散成上百个日期目录。
        self.var_by_date = tk.BooleanVar(value=False)
        self.var_cleanup = tk.BooleanVar(value=False)
        self.var_quality = tk.IntVar(value=2)
        self.var_status = tk.StringVar(value="就绪")
        self.var_count = tk.StringVar(value="尚未添加文件")

        self.state = {"files": [], "out_root": "", "src_label": "",
                      "opts": {}, "done": [], "updated": 0.0}
        self.resume_banner = None

        self._build()
        self._bind_estimate()
        self._pump()
        self._check_env()
        self._offer_resume()

    # ---------- 界面构建
    def _build(self):
        pad = 16
        head = tk.Frame(self, bg=self.BG)
        head.pack(fill="x", padx=pad, pady=(pad, 0))
        tk.Label(head, text="LIVP 实况照片转换器", font=("Microsoft YaHei UI", 15, "bold"),
                 bg=self.BG, fg=self.FG).pack(side="left")
        tk.Label(head, text="批量拆分为 JPG 照片 + MP4 视频", font=("Microsoft YaHei UI", 9),
                 bg=self.BG, fg=self.MUTED).pack(side="left", padx=(10, 0), pady=(6, 0))

        # --- 源
        f1 = self._card()
        f1.pack(fill="x", padx=pad, pady=(12, 0))
        self._card_anchor = f1
        self._title(f1, "① 选择要转换的文件")
        b1 = f1._body
        tk.Label(b1, text="来源", bg=self.CARD, fg=self.MUTED, width=6,
                 font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="w")
        e1 = tk.Entry(b1, textvariable=self.var_src, bg=self.CARD, fg=self.FG, relief="flat",
                      font=("Microsoft YaHei UI", 9), state="readonly")
        e1.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(0, 3), ipady=4)
        self._btn(b1, "选择文件", lambda: self.pick_files()).grid(row=0, column=2, padx=(0, 6))
        self._btn(b1, "选择文件夹", lambda: self.pick_folder(), primary=True).grid(row=0, column=3)
        self._btn(b1, "清空", self.clear_files).grid(row=0, column=4, padx=(6, 0))

        # --- 输出
        f2 = self._card()
        f2.pack(fill="x", padx=pad, pady=(10, 0))
        self._title(f2, "② 选择输出位置")
        b2 = f2._body
        tk.Label(b2, text="输出", bg=self.CARD, fg=self.MUTED, width=6,
                 font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, sticky="w")
        e2 = tk.Entry(b2, textvariable=self.var_out, bg=self.CARD, fg=self.FG, relief="flat",
                      font=("Microsoft YaHei UI", 9))
        e2.grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(0, 3), ipady=4)
        self._btn(b2, "浏览", self.pick_out, primary=True).grid(row=0, column=2)
        self._chk(b2, "保留原有子文件夹结构", self.var_preserve).grid(
            row=1, column=1, sticky="w", pady=(4, 0))
        self._chk(b2, "跳过已转换（输出已存在时不重复处理）", self.var_skip).grid(
            row=1, column=2, sticky="w", pady=(4, 0))

        # --- 选项
        f3 = self._card()
        f3.pack(fill="x", padx=pad, pady=(10, 0))
        self._title(f3, "③ 转换选项")
        b3 = f3._body
        opts = tk.Frame(b3, bg=self.CARD)
        opts.grid(row=0, column=0, sticky="w")
        self._chk(opts, "输出 JPG 照片（必选）", tk.BooleanVar(value=True), state="disabled") \
            .grid(row=0, column=0, sticky="w", padx=(0, 16))
        self._chk(opts, "输出 MP4 视频", self.var_mp4).grid(row=0, column=1, sticky="w", padx=(0, 16))
        self._chk(opts, "输出 GIF 动图", self.var_gif,
                 command=self._on_gif_toggle).grid(row=0, column=2, sticky="w")
        self._chk(opts, "同时保留原始 HEIC/MOV", self.var_keep).grid(row=1, column=0, sticky="w", padx=(0, 16), pady=(8, 0))
        self._chk(opts, "按拍摄时间命名（如 20260315_102333）", self.var_by_time,
                 command=self._on_by_time_toggle).grid(row=1, column=1, sticky="w", pady=(8, 0))
        self._chk(opts, "按日期分文件夹（在相册目录下再分 2026-03-15/）", self.var_by_date).grid(
            row=2, column=0, sticky="w", padx=(0, 16), pady=(6, 0))
        self._chk(opts, "转换后清理源 .livp（送入回收站）", self.var_cleanup,
                 command=self._on_cleanup_toggle).grid(
            row=2, column=1, sticky="w", pady=(6, 0))

        qf = tk.Frame(b3, bg=self.CARD)
        qf.grid(row=1, column=0, sticky="w", pady=(8, 0))
        tk.Label(qf, text="照片质量", bg=self.CARD, fg=self.MUTED, width=6,
                 font=("Microsoft YaHei UI", 9)).grid(row=0, column=0)
        self.qual_text = tk.Label(qf, text="很高", bg=self.CARD, fg=self.FG, width=4,
                                  font=("Microsoft YaHei UI", 9))
        self.qual_text.grid(row=0, column=1)
        self.scale = ttk.Scale(qf, from_=1, to=8, orient="horizontal", length=190,
                                variable=self.var_quality, command=self._update_quality)
        self.scale.grid(row=0, column=2, padx=(0, 10))
        tk.Label(qf, text="1 = 最高画质（文件大）    8 = 最小体积", bg=self.CARD, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 8)).grid(row=0, column=3, sticky="w")

        # --- 操作
        f4 = self._card()
        f4.pack(fill="x", padx=pad, pady=(10, 0))
        b4 = f4._body
        tk.Label(b4, textvariable=self.var_count, bg=self.CARD, fg=self.FG,
                 font=("Microsoft YaHei UI", 9, "bold")).grid(row=0, column=0, sticky="w")
        self.btn_start = self._btn(b4, "开始转换", self.start, primary=True, big=True)
        self.btn_start.grid(row=0, column=1, padx=(10, 0))
        self.btn_stop = self._btn(b4, "停止", self.stop)
        self.btn_stop.grid(row=0, column=2, padx=(6, 0))
        self.btn_open = self._btn(b4, "打开输出文件夹", self.open_out)
        self.btn_open.grid(row=0, column=3, padx=(6, 0))
        self.btn_clear = self._btn(b4, "清除记录", self.clear_record)
        self.btn_clear.grid(row=0, column=4, padx=(6, 0))
        self.btn_sheet = self._btn(b4, "生成联系表", self.make_contact_sheet)
        self.btn_sheet.grid(row=0, column=5, padx=(6, 0))

        # 联系表进度：与上方转换进度语义不同，必须独立一条。
        # 首次生成 893 张缩略图实测约 189 秒，没有它用户面对的是
        # 一个三分钟不响应的窗口。
        sh = tk.Frame(b4, bg=self.CARD)
        sh.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(7, 0))
        self.sheet_bar = ttk.Progressbar(sh, mode="determinate", maximum=100,
                                         length=180)
        self.sheet_bar.pack(side="left")
        self.var_sheet = tk.StringVar(value="")
        tk.Label(sh, textvariable=self.var_sheet, bg=self.CARD, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(8, 0))
        self.btn_sheet_stop = self._btn(sh, "停止", self.stop_sheet)
        self.btn_sheet_stop.pack(side="right")
        # 未开始生成时整行隐藏：一条空的进度槽只是噪音。
        # 用 grid_remove 而非 pack_forget —— 外层是 grid 管理的。
        self._sheet_row = sh
        sh.grid_remove()

        # 耗时预估
        self.var_est = tk.StringVar()
        est = tk.Label(b4, textvariable=self.var_est, bg=self.CARD, fg=self.MUTED,
                       font=("Microsoft YaHei UI", 8), anchor="w")
        est.grid(row=2, column=0, columnspan=6, sticky="w", pady=(5, 0))

        # --- 进度
        f5 = self._card()
        f5.pack(fill="both", expand=True, padx=pad, pady=(10, pad))
        self._title(f5, "转换日志")
        b5 = f5._body
        self.bar = ttk.Progressbar(b5, mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=(0, 6))

        # 筛选行：893 行日志里失败的可能只有 4 行，靠肉眼翻是找不到的
        flt = tk.Frame(b5, bg=self.CARD)
        flt.pack(fill="x", pady=(0, 6))
        self.var_filter = tk.StringVar(value="all")
        self.log_counts = {"all": 0, "ok": 0, "fail": 0, "skip": 0}
        self.filter_btns = {}
        for key, label in (("all", "全部"), ("ok", "成功"),
                           ("fail", "失败"), ("skip", "跳过")):
            b = tk.Button(flt, text=label, command=lambda k=key: self._set_filter(k),
                          bg=self.CARD, fg=self.FG, relief="flat", bd=0,
                          font=("Microsoft YaHei UI", 9), padx=10, pady=2,
                          cursor="hand2", activebackground="#eff6ff")
            b.pack(side="left")
            self.filter_btns[key] = b
        self.var_filter_label = tk.StringVar(value="尚无记录")
        tk.Label(flt, textvariable=self.var_filter_label, bg=self.CARD, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(10, 0))
        self.btn_copy_fail = self._btn(flt, "复制失败项", self._copy_failures)
        self.btn_copy_fail.pack(side="right")

        self.log = ScrolledText(b5, bg=self.CARD, fg=self.FG, font=("Consolas", 9),
                                relief="flat", wrap="none", state="disabled")
        self.log.pack(fill="both", expand=True)
        for _tag, _color in self.LOG_TAGS:
            self.log.tag_config(_tag, foreground=_color)
        self._sync_filter_buttons()
        tk.Label(self, textvariable=self.var_status, bg=self.BG, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 8), anchor="w").pack(fill="x", padx=pad, pady=(0, 8))

        b1.columnconfigure(1, weight=1)
        b2.columnconfigure(1, weight=1)

    def _card(self):
        """白色圆角卡片。返回内容容器（内部统一用 grid），标题用 pack。
        注意：同一个 Frame 内不能混用 pack 和 grid，故标题与内容分层。"""
        outer = tk.Frame(self, bg=self.CARD, highlightbackground=self.BORDER,
                         highlightthickness=1, bd=0)
        body = tk.Frame(outer, bg=self.CARD)
        body.pack(fill="both", expand=True, padx=12, pady=(10, 12))
        outer._body = body
        return outer

    def _title(self, parent, text):
        """标题条：pack 在卡片顶层，内部控件请用 parent._body 做 grid。"""
        tk.Label(parent, text=text, bg=self.CARD, fg=self.FG,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(
            anchor="w", padx=12, pady=(10, 0))

    def _btn(self, parent, text, cmd, primary=False, big=False):
        style = "Accent.TButton" if primary else "Norm.TButton"
        b = ttk.Button(parent, text=text, command=cmd, style=style)
        if big:
            b.configure(padding=(18, 7))
        return b

    def _chk(self, parent, text, var, state="normal", command=None):
        return tk.Checkbutton(parent, text=text, variable=var, bg=self.CARD, fg=self.FG,
                              activebackground=self.CARD, activeforeground=self.FG,
                              selectcolor=self.CARD, font=("Microsoft YaHei UI", 9),
                              state=state, command=command)

    def _update_quality(self, v):
        names = {1: "极高", 2: "很高", 3: "高", 4: "较高", 5: "中", 6: "较低", 7: "低", 8: "很小"}
        self.qual_text.config(text=names.get(int(float(v)), "-"))

    def _on_gif_toggle(self):
        """勾选 GIF 时提示体积代价 —— 实测约 6MB/个，量大时占用惊人。"""
        if not self.var_gif.get():
            return
        n = len(self.files)
        if n == 0:
            return
        est = n * 6      # 实测单文件约 6MB
        if est < 200:
            hint = "当前 %d 个文件，预计占用约 %d MB。" % (n, est)
        else:
            hint = ("当前 %d 个文件，预计占用约 %.1f GB，且生成速度明显变慢。"
                    % (n, est / 1024))
        if not messagebox.askyesno("确认生成 GIF？",
                                   "GIF 动图体积较大、生成较慢。\n\n%s\n\n是否继续？"
                                   % hint):
            self.var_gif.set(False)

    # ---------- 样式
    def _on_by_time_toggle(self):
        """勾选「按拍摄时间命名」时说明时间从哪来，避免用户以为是文件修改时间。"""
        if not self.var_by_time.get():
            return
        if len(self.files) < 2:
            return
        if not messagebox.askyesno(
                "命名规则说明",
                "开启后将按「拍摄时间」重命名产物：\n\n"
                "• 优先读取照片 EXIF 里的拍摄时间（最准确）\n"
                "• 读不到时从文件名推断（如 IMG_20260315_102333）\n"
                "• 仍读不到才用文件修改时间\n\n"
                "同一秒内的连拍会自动追加序号，不会互相覆盖。\n\n"
                "是否继续？"):
            self.var_by_time.set(False)

    # ---------- 断点续传
    def _show_resume(self, text):
        """顶部显示一条可关闭的续传提示。"""
        self._hide_resume()
        bar = tk.Frame(self, bg="#eff6ff", highlightbackground="#bfdbfe",
                       highlightthickness=1)
        lbl = tk.Label(bar, text=text, bg="#eff6ff", fg="#1e40af",
                       font=("Microsoft YaHei UI", 9), anchor="w", justify="left")
        lbl.pack(side="left", fill="x", expand=True, padx=(12, 6), pady=8)
        b = tk.Button(bar, text="✕", command=self._hide_resume, bg="#eff6ff",
                      fg="#1e40af", relief="flat", bd=0, font=("Microsoft YaHei UI", 9))
        b.pack(side="right", padx=(0, 8))
        b2 = tk.Button(bar, text="清除记录", command=self.clear_record, bg="#eff6ff",
                       fg="#1e40af", relief="flat", bd=0, font=("Microsoft YaHei UI", 8))
        b2.pack(side="right", padx=(0, 2))
        bar.pack(fill="x", padx=16, pady=(12, 0), before=self._first_card())
        self.resume_banner = bar

    def _hide_resume(self):
        if self.resume_banner is not None:
            try:
                self.resume_banner.destroy()
            except Exception:
                pass
            self.resume_banner = None

    def _first_card(self):
        """选项区之后的第一张卡片，用于 pack(before=...) 定位。"""
        return getattr(self, "_card_anchor", None) or self.winfo_children()[0]

    def _offer_resume(self):
        """启动时读取上次任务，若有未完成项就提示继续。"""
        # 旧版本状态文件的选项语义可能已变（如「按日期分文件夹」默认值调整），
        # 版本不符时 livp_state 会返回空白，这里如实告知用户记录已被丢弃
        stale = False
        try:
            import json as _json
            with open(livp_state.STATE_PATH, "r", encoding="utf-8") as f:
                _d = _json.load(f)
            stale = isinstance(_d, dict) and _d.get("version") != livp_state.STATE_VERSION
        except Exception:
            stale = False

        st = livp_state.load()
        if stale:
            self.write_log("检测到旧版本的任务记录，选项默认值已变更，"
                           "该记录已忽略（不影响任何已转换的文件）。")
        alive, lost = livp_state.prune_missing(st.get("files"))
        if not alive:
            if lost:
                self.write_log("上次任务的 %d 个源文件已不存在，已忽略。" % lost)
            return

        self.state = st
        done = [f[0] for f in alive if f[0] in set(st.get("done") or [])]
        left = len(alive) - len(done)
        if left == 0:
            # 全部完成也要把文件填回列表，否则日志说「可直接开始新任务」
            # 但列表其实是空的，用户点开始只会得到「请先选择文件」
            self.files = list(alive)
            if not self.var_src.get():
                self.var_src.set(st.get("src_label") or "")
            if st.get("out_root") and not self.var_out.get():
                self.var_out.set(st["out_root"])
            self._restore(st, alive)
            self.write_log("已恢复上次任务：%d 个文件均已完成，可直接开始新任务。"
                           % len(alive))
            return

        self.files = list(alive)
        self.var_src.set(st.get("src_label") or "")
        if st.get("out_root"):
            self.var_out.set(st["out_root"])
        self._restore(st, alive, skip_done=False)
        if lost:
            self.write_log("上次任务中 %d 个源文件已丢失，已从列表移除。" % lost)
        self._show_resume(
            "发现上次未完成的任务：共 %d 个文件，已完成 %d 个，还剩 %d 个。"
            "点「开始转换」即可接着上次继续。\n输出位置：%s"
            % (len(alive), len(done), left, st.get("out_root") or "（未设置）"))

    def _restore(self, st, alive, skip_done=True):
        """把状态里的选项回填到界面。"""
        o = st.get("opts") or {}
        for key, var in (("mp4", self.var_mp4), ("gif", self.var_gif),
                         ("keep_heic", self.var_keep), ("skip_existing", self.var_skip),
                         ("by_time", self.var_by_time), ("by_date", self.var_by_date)):
            if key in o:
                var.set(bool(o[key]))
        # 清理源文件是破坏性选项，故意不随记录恢复：
        # 重开程序时静默勾上等于替用户做了删文件的决定，必须每次重新确认。
        self.var_cleanup.set(False)
        try:
            self.var_quality.set(int(o.get("quality", 2)))
        except Exception:
            pass
        self._refresh()

    def _collect_opts(self):
        return {
            "mp4": self.var_mp4.get(),
            "gif": self.var_gif.get(),
            "keep_heic": self.var_keep.get(),
            # 「保留原始文件」同时保留 HEIC 与 MOV，与界面文字保持一致
            "keep_mov": self.var_keep.get(),
            "quality": self.var_quality.get(),
            "skip_existing": self.var_skip.get(),
            "by_time": self.var_by_time.get(),
            "by_date": self.var_by_date.get(),
            "cleanup": self.var_cleanup.get(),
        }

    def _persist(self):
        """把当前任务落盘，供下次续传。"""
        self.state["files"] = [list(f) for f in self.files]
        self.state["out_root"] = self.var_out.get().strip()
        self.state["src_label"] = self.var_src.get()
        self.state["opts"] = self._collect_opts()
        livp_state.save(self.state)

    def clear_record(self):
        """清除续传记录。"""
        if messagebox.askyesno("清除记录",
                               "将删除本机的任务记录（不影响任何已转换的文件）。\n\n"
                               "下次启动不会自动恢复上次的列表。\n\n是否继续？"):
            livp_state.clear()
            self.state = {"files": [], "out_root": "", "src_label": "",
                          "opts": {}, "done": [], "updated": 0.0}
            self._hide_resume()
            self._refresh_estimate()
            self.write_log("已清除任务记录。")

    def _on_cleanup_toggle(self):
        """勾选「清理源文件」时明确告知风险，并要求二次确认。"""
        if not self.var_cleanup.get():
            return
        if not self.files:
            return

        n = len(self.files)
        size = livp_cleanup.total_size(p for p, _ in self.files)
        self.write_log("已启用清理源文件：本次涉及 %d 个 .livp，共 %s。"
                       % (n, livp_cleanup.fmt_size(size)))

        if not messagebox.askyesno(
                "清理源文件 — 请先了解风险",
                "勾选后，转换完成的 .livp 源文件会被移入【回收站】。\n\n"
                "涉及文件：%d 个\n占用空间：%s\n\n"
                "请注意以下三点：\n\n"
                "• 文件进入回收站后【依然占用磁盘空间】，并不会立刻释放。\n"
                "  需要你手动清空回收站才会真正腾出空间。\n\n"
                "• 一旦清空回收站，源文件【无法恢复】。\n"
                "  .livp 是唯一能还原 HEIC 原图与原始视频的来源。\n\n"
                "• 若 JPG 质量设置偏高（数字偏小），有损压缩可能让你\n"
                "  日后悔不当初。\n\n"
                "本工具的保护措施：\n"
                "• 仅在全部文件转换成功后才清理\n"
                "• 中途停止或失败的文件一律保留\n"
                "• 逐个确认产物已落盘后才删除对应源文件\n\n"
                "是否继续启用？" % (n, livp_cleanup.fmt_size(size))):
            self.var_cleanup.set(False)
            self.write_log("已取消启用清理源文件。")
            return

        if not messagebox.askokcancel(
                "再次确认",
                "最后确认：\n\n"
                "转换完成后，%d 个源文件将进入回收站。\n\n"
                "清空回收站后不可恢复。\n\n"
                "点击「确定」继续，或「取消」关闭该选项。"
                % n):
            self.var_cleanup.set(False)
            self.write_log("已取消启用清理源文件。")

    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Norm.TButton", font=("Microsoft YaHei UI", 9), padding=(10, 5))
        st.configure("Accent.TButton", font=("Microsoft YaHei UI", 9, "bold"),
                     padding=(10, 5), background=self.ACCENT, foreground="#ffffff",
                     bordercolor=self.ACCENT, lightcolor=self.ACCENT, darkcolor=self.ACCENT)
        st.map("Accent.TButton",
               background=[("active", "#1d4ed8"), ("disabled", "#a8b8d8")])
        st.configure("TProgressbar", background=self.ACCENT, troughcolor="#e5e7eb",
                     bordercolor="#e5e7eb", lightcolor=self.ACCENT, darkcolor=self.ACCENT)

    # ---------- 文件选择
    def pick_files(self):
        paths = filedialog.askopenfilenames(
            title="选择 LIVP 文件（可多选）",
            filetypes=[("LIVP 实况照片", "*.livp"), ("所有文件", "*.*")])
        if not paths:
            return
        if len(paths) > 1:
            base = os.path.commonpath([os.path.dirname(p) for p in paths])
        else:
            base = os.path.dirname(paths[0])
        known = {p for p, _ in self.files}
        for p in paths:
            if p in known:
                continue
            rel = ""
            if self.var_preserve.get():
                d = os.path.dirname(p)
                rel = "" if os.path.normcase(d) == os.path.normcase(base) \
                    else os.path.relpath(d, base)
            self.files.append((p, rel))
            known.add(p)
        self.var_src.set("%d 个文件（来自 %s）" % (len(paths), base))
        if not self.var_out.get():
            self.var_out.set(os.path.join(os.path.commonpath(list(paths)), "转换结果"))
        self._refresh()

    def pick_folder(self):
        d = filedialog.askdirectory(title="选择文件夹（自动查找其中所有 .livp）")
        if not d:
            return
        found = []
        for root, _dirs, files in os.walk(d):
            for fn in files:
                if fn.lower().endswith(".livp"):
                    found.append(os.path.join(root, fn))
        if not found:
            messagebox.showinfo("未找到", "该文件夹中没有 .livp 文件。")
            return
        known = {p for p, _ in self.files}
        for p in sorted(found):
            if p in known:
                continue
            rel = ""
            if self.var_preserve.get():
                rel = os.path.relpath(os.path.dirname(p), d)
                rel = "" if rel == "." else rel
            self.files.append((p, rel))
            known.add(p)
        self.var_src.set(d)
        if not self.var_out.get():
            self.var_out.set(os.path.join(d, "转换结果"))
        self._refresh()

    def clear_files(self):
        self.files = []
        self._refresh()

    def _refresh(self):
        done = set(self.state.get("done") or [])
        todo = len([f for f in self.files if f[0] not in done])
        if todo and todo < len(self.files):
            self.var_count.set("待转换：%d 个（另有 %d 个上次已完成）"
                               % (todo, len(self.files) - todo))
        else:
            self.var_count.set("待转换：%d 个 .livp 文件" % len(self.files))
        self._refresh_estimate()

    def _refresh_estimate(self):
        """按当前文件数与选项重算耗时/体积预估。"""
        if not hasattr(self, "var_est"):
            return
        # done 里可能含已不在列表中的源文件，只按仍存在的部分计
        alive = {p for p, _ in self.files}
        done = len([d for d in (self.state.get("done") or []) if d in alive])
        self.var_est.set(
            livp_estimate.describe(len(self.files), self._collect_opts(), done))

    def _bind_estimate(self):
        """把预估绑定到所有会影响代价的选项上。

        注意：var_cleanup 不影响耗时与产物体积（只影响源文件去向），
        故不纳入。
        """
        for var in (self.var_mp4, self.var_gif, self.var_keep,
                    self.var_quality, self.var_skip, self.var_by_time):
            var.trace_add("write", lambda *_a: self._refresh_estimate())

    def pick_out(self):
        d = filedialog.askdirectory(title="选择输出文件夹")
        if d:
            self.var_out.set(d)

    def open_out(self):
        d = self.var_out.get().strip()
        if not d or not os.path.isdir(d):
            messagebox.showwarning("提示", "输出文件夹不存在或尚未生成。")
            return
        try:
            os.startfile(d)
        except Exception:
            messagebox.showinfo("输出位置", d)

    def make_contact_sheet(self):
        """扫描输出目录生成 HTML 联系表。

        生成过程放到后台线程：首次为 893 张生成缩略图实测约 189 秒，
        放在主线程会让窗口冻结三分钟并被系统判定「未响应」。
        """
        if self.sheet_worker and self.sheet_worker.is_alive():
            return
        d = self.var_out.get().strip()
        if not d or not os.path.isdir(d):
            messagebox.showwarning("提示", "输出文件夹不存在或尚未生成。")
            return
        if not FFMPEG:
            messagebox.showerror("缺少 ffmpeg", "生成缩略图需要 ffmpeg，请先安装。")
            return

        entries = livp_contact.scan(d)
        if not entries:
            messagebox.showinfo("没有可展示的照片",
                                "输出文件夹里还没有 JPG 文件。\n请先执行一次转换。")
            return

        # 预扫描只需毫秒级，却能让用户提前知道要等多久：
        # 首次生成与缓存命中（实测约 2 秒）体感差别巨大。
        total, miss = livp_contact.count_missing(entries, d)
        if miss == 0:
            self.write_log("联系表：%d 张缩略图均在缓存中，直接生成页面…" % total)
        else:
            self.write_log("联系表：共 %d 张，其中 %d 张需新建缩略图"
                           "（首次生成较慢，之后点一次很快）。" % (total, miss))

        self.sheet_bar.config(maximum=total, value=0)
        self.var_sheet.set("准备中…")
        self.var_status.set("正在生成联系表…")
        self._sheet_row.grid()
        self.btn_sheet.config(state="disabled")
        self.btn_sheet_stop.config(state="normal")

        self.sheet_worker = SheetWorker(d, entries, FFMPEG, self.q)
        self.sheet_worker.start()

    def stop_sheet(self):
        """中途放弃生成。已缓存的缩略图保留，下次接着用。"""
        w = self.sheet_worker
        if w and w.is_alive():
            w.stop()
            self.var_sheet.set("正在停止…")
            self.var_status.set("正在停止生成联系表…")

    def _sheet_finished(self, kind):
        """联系表生成结束（成功 / 取消 / 失败）的统一收尾。

        消息固定 5 元组：(事件, path, stopped, err, stats)。
        err 与 stats 只能有一个非 None —— 曾因两者位置不一致，
        把 stats 当成错误信息，导致正常完成被显示成「生成失败」。
        """
        path, stopped = kind[1], kind[2]
        err = kind[3] if len(kind) > 3 else None
        stats = kind[4] if len(kind) > 4 else None
        # 防御：err 只接受字符串，stats 只接受 dict。
        # 曾因两者元组位置不一致，把 stats 当错误显示成「生成失败」。
        if err is not None and not isinstance(err, str):
            err = None
        if stats is not None and not isinstance(stats, dict):
            stats = None

        self.btn_sheet.config(state="normal")
        self.btn_sheet_stop.config(state="disabled")
        self.var_status.set("就绪")

        if stopped or path is None:
            done_n = int(self.sheet_bar["maximum"])
            got = int(self.sheet_bar["value"])
            self.var_sheet.set("已停止（%d / %d）" % (got, done_n))
            self.write_log("已停止生成联系表：完成 %d / %d。"
                           "已生成的缩略图保留，下次点「生成联系表」会接着用。"
                           % (got, done_n))
            return
        if err:
            self.var_sheet.set("生成失败")
            self.write_log("联系表生成失败：%s" % err, "fail")
            messagebox.showerror("生成失败", str(err))
            return

        self.sheet_bar.config(value=self.sheet_bar["maximum"])
        self.var_sheet.set("已生成 %d 张" % (stats or {}).get("photos", 0))
        cached = (stats or {}).get("cached", 0)
        gen = (stats or {}).get("generated", 0)
        self.write_log("联系表已生成：%s" % path, "meta")
        if gen or cached:
            self.write_log("  缩略图：新建 %d 张，复用缓存 %d 张" % (gen, cached),
                           "meta")
        self.write_log("  用浏览器打开即可浏览，点击看大图，"
                       "支持翻页/滚动、搜索与灯箱。", "meta")
        try:
            os.startfile(path)
        except Exception:
            messagebox.showinfo("联系表已生成", path)

    # ---------- 运行控制
    def _check_env(self):
        if FFMPEG:
            self.write_log("ffmpeg 引擎就绪：" + FFMPEG)
            return
        self.write_log("【错误】未找到 ffmpeg，无法转换。")
        self.btn_start.config(state="disabled")
        messagebox.showerror(
            "缺少 ffmpeg，无法转换",
            "本工具依赖 ffmpeg 来转换图片和视频，但当前电脑上没有找到它。\n\n"
            "请先安装 ffmpeg，然后重新打开本程序：\n\n"
            "• Windows：打开 PowerShell，执行\n"
            "    winget install Gyan.FFmpeg\n"
            "• 或前往官网下载：https://ffmpeg.org/download.html\n\n"
            "也可以把 ffmpeg.exe 放到本程序所在的文件夹里。")

    def start(self):
        if not self.files:
            messagebox.showwarning("提示", "请先选择要转换的文件或文件夹。")
            return
        out = self.var_out.get().strip()
        if not out:
            messagebox.showwarning("提示", "请选择输出文件夹。")
            return
        # 防止把结果写回源目录导致与 .livp 混杂
        src_dirs = {os.path.normcase(os.path.dirname(os.path.abspath(p)))
                    for p, _ in self.files}
        if os.path.normcase(os.path.abspath(out)) in src_dirs:
            messagebox.showwarning("提示", "输出文件夹不能和源文件放在同一目录，\n"
                                          "请另选一个位置，避免结果与原文件混杂。")
            return

        opts = self._collect_opts()
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        # 新批次从零计数，否则会叠加上一轮的数字
        for k in self.log_counts:
            self.log_counts[k] = 0
        self._sync_filter_buttons()
        self.write_log("开始转换 %d 个文件 → %s" % (len(self.files), out), "meta")
        if opts["by_time"]:
            self.write_log("命名：按拍摄时间（EXIF 优先，文件名兜底，修改时间最后兜底）", "meta")
        if opts["by_date"] and opts["by_time"]:
            self.write_log("目录：按拍摄日期自动分层", "meta")
        self.write_log("-" * 58, "meta")

        # 新任务重开进度；续传则沿用已记录项
        self.state["done"] = [d for d in (self.state.get("done") or [])
                              if d in {p for p, _ in self.files}]
        self._persist()

        self.btn_start.config(state="disabled")
        self.bar.config(maximum=len(self.files), value=0)

        self.worker = Worker(list(self.files), out, opts, self.q)
        self.worker.start()

    def stop(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self.var_status.set("正在停止…")

    def _pump(self):
        try:
            while True:
                kind = self.q.get_nowait()
                if kind[0] == "total":
                    self.bar.config(maximum=kind[1])
                elif kind[0] == "sheet_total":
                    _, _total, miss = kind
                    self.sheet_bar.config(maximum=_total, value=0)
                    self.var_sheet.set("0 / %d（需新建 %d 张）" % (_total, miss))
                elif kind[0] == "sheet_progress":
                    _, done, name = kind
                    self.sheet_bar.config(value=done)
                    self.var_sheet.set("%d / %d  %s"
                                       % (done, self.sheet_bar["maximum"], name))
                elif kind[0] == "sheet_done":
                    self._sheet_finished(kind)
                elif kind[0] == "item":
                    self.var_status.set("(%d/%d) %s" % (kind[1], self.bar["maximum"],
                                                         os.path.basename(kind[2])))
                elif kind[0] == "progress":
                    # 每完成一个就落盘，中途关闭程序也不丢进度
                    if kind[1] not in self.state["done"]:
                        self.state["done"].append(kind[1])
                        self._persist()
                elif kind[0] == "done":
                    _, idx, src, made = kind
                    self.bar.config(value=idx)
                    # 「已存在，跳过」与真实转换成功要分开归类，
                    # 否则点「成功」会混进几百条其实没转换的记录
                    if made == "已存在，跳过":
                        self.write_log("[%d] 跳过 %s  →  %s"
                                       % (idx, os.path.basename(src), made), "skip")
                    else:
                        self.write_log("[%d] OK   %s  →  %s"
                                       % (idx, os.path.basename(src), made), "ok")
                elif kind[0] == "fail":
                    _, idx, src, err = kind
                    self.bar.config(value=idx)
                    self.write_log("[%d] 失败 %s  →  %s"
                                   % (idx, os.path.basename(src), err), "fail")
                elif kind[0] == "cleanup_keep":
                    kept = kind[1]
                    self.write_log("有 %d 个文件的产物未确认落盘，"
                                   "其源文件已保留：" % len(kept), "meta")
                    for s in kept[:5]:
                        self.write_log("· " + os.path.basename(s), "skip")
                    if len(kept) > 5:
                        self.write_log("  …另有 %d 个" % (len(kept) - 5), "meta")
                elif kind[0] == "finish":
                    _, ok, fail, skip, stopped = kind[:5]
                    no_time = kind[5] if len(kind) > 5 else 0
                    cleanup = kind[6] if len(kind) > 6 else (0, 0, [])
                    self.write_log("-" * 58, "meta")
                    parts = ["成功 %d" % ok, "失败 %d" % fail]
                    if skip:
                        parts.append("跳过 %d" % skip)
                    if stopped:
                        parts.append("已手动停止")
                    self.write_log("完成：" + "，".join(parts), "meta")
                    if no_time:
                        self.write_log(
                            "⚠ 有 %d 个文件未能读到拍摄时间（EXIF 与文件名都没有），"
                            "已保留原文件名。" % no_time, "meta")
                        self.write_log("  文件修改时间在传输/备份后往往失真，"
                                       "因此未用它生成文件名。", "meta")

                    cok, cfail, cerrs = cleanup
                    if self.var_cleanup.get() or cok or cfail:
                        if stopped:
                            self.write_log("已手动停止，源文件全部保留，未执行清理。", "meta")
                        elif fail:
                            self.write_log("存在 %d 个失败文件，本次不清理任何源文件。"
                                           % fail, "meta")
                        elif cok:
                            self.write_log("已将 %d 个源 .livp 移入回收站。"
                                           % cok, "meta")
                            self.write_log("  回收站仍占用磁盘空间，"
                                           "需要时请手动清空回收站；清空后不可恢复。", "meta")
                        if cfail:
                            self.write_log("⚠ %d 个源文件未能删除，已保留：" % cfail, "meta")
                            for e in cerrs[:5]:
                                self.write_log("  · " + e, "meta")
                            if len(cerrs) > 5:
                                self.write_log("  …另有 %d 条" % (len(cerrs) - 5), "meta")

                    self.var_status.set("已停止" if stopped else "全部完成")
                    self.btn_start.config(state="normal")
                    self.var_cleanup.set(False)   # 清理只对本次生效
                    # 本次已完成的数量进入记录，预估随之下降
                    self._refresh_estimate()
                    # 汇总落定：让筛选按钮的计数与最终结果一致
                    self._sync_filter_buttons()
                    if (ok or skip) and not stopped:
                        msg = "成功 %d 个" % ok
                        if fail:
                            msg += "，失败 %d 个" % fail
                        if skip:
                            msg += "，跳过 %d 个（已存在）" % skip
                        if cok:
                            msg += "\n\n另有 %d 个源文件已移入回收站（需手动清空回收站才会释放空间）" % cok
                        messagebox.showinfo("转换完成", msg + "。\n\n输出位置：%s"
                                            % self.var_out.get())
        except queue.Empty:
            pass
        self.after(120, self._pump)

    # ---------- 日志
    # 每类日志一个 tkinter tag：既决定颜色，也决定筛选时是否可见。
    # 「批次」类（meta）在任何筛选下都显示 —— 用户筛出 4 条失败时，
    # 仍需看到「共 893 个，失败 4」这个上下文，否则不知道漏了什么。
    LOG_TAGS = (
        ("ok", "#15803d"),# 成功：绿色
        ("fail", "#dc2626"),    # 失败：红色
        ("skip", "#b45309"),    # 跳过：橙色
        ("meta", "#2563eb"),    # 批次信息：蓝色
        ("plain", "#1f2328"),   # 其他：正文色
    )

    def write_log(self, text, kind="plain"):
        """追加一行日志。kind 决定颜色与筛选归属。

        kind: ok / fail / skip / meta / plain
        只有 ok、fail、skip 属于「逐文件结果」，参与筛选；
        meta 与 plain 始终可见。
        """
        self.log.config(state="normal")
        self.log.insert("end", text + "\n", kind)
        # 逐文件结果行数会增长，超量时裁剪头部，避免 Text 无限膨胀变慢
        self._trim_log()
        self.log.see("end")
        self.log.config(state="disabled")
        # 转换过程中也要让按钮上的计数实时变化，便于中途点「失败」
        if kind in self._FILTERABLE:
            self.log_counts[kind] = self.log_counts.get(kind, 0) + 1
            self.log_counts["all"] = sum(self.log_counts[t]
                                         for t in self._FILTERABLE)
            self._sync_filter_buttons()

    def _trim_log(self, keep=4000):
        """只保留最近若干行，防止上万行日志拖慢界面。"""
        n = int(self.log.index("end-1c").split(".")[0])
        if n <= keep:
            return
        self.log.delete("1.0", "%d.0" % (n - keep))

    # ---------- 日志筛选
    # 关键约定：meta 与 plain 行在任何筛选下都可见。
    # 用户筛出 4 条失败时，仍需看到「共 893 个，失败 4」这类批次上下文，
    # 否则会误以为整批只有 4 个文件。
    _FILTERABLE = ("ok", "fail", "skip")

    def _set_filter(self, key):
        self.var_filter.set(key)
        self._apply_filter()

    def _apply_filter(self):
        """按当前筛选显示/隐藏逐文件结果行，并刷新计数。"""
        key = self.var_filter.get()
        self.log.config(state="normal")
        # 先全部显示，再隐藏不匹配的，避免反复切换时状态错乱。
        # 注意：tag_cget 返回的是 Tcl 布尔，值是字符串 "0"/"1"，
        # 不能写成 `if app.log.tag_cget(...)` —— 非空字符串恒为真。
        for t in self._FILTERABLE:
            self.log.tag_config(t, elide=False)
        if key != "all":
            for t in self._FILTERABLE:
                if t != key:
                    self.log.tag_config(t, elide=True)
        self.log.config(state="disabled")
        self._sync_filter_buttons()

    def _sync_filter_buttons(self):
        """按钮高亮 + 文案里的计数。"""
        cur = self.var_filter.get()
        for key, b in self.filter_btns.items():
            if key == cur:
                b.config(bg="#eff6ff", fg=self.ACCENT,
                         font=("Microsoft YaHei UI", 9, "bold"))
            else:
                b.config(bg=self.CARD, fg=self.FG,
                         font=("Microsoft YaHei UI", 9))
            n = self.log_counts.get(key, 0)
            b.config(text=("全部" if key == "all" else
                           ("成功" if key == "ok" else
                            ("失败" if key == "fail" else "跳过")))
                     + (" (%d)" % n if n else ""))

        total = self.log_counts["all"]
        if total == 0:
            self.var_filter_label.set("尚无记录")
        elif self.var_filter.get() == "all":
            self.var_filter_label.set("共 %d 条记录" % total)
        else:
            self.var_filter_label.set(
                "显示 %d / %d 条逐文件记录"
                % (self.log_counts.get(self.var_filter.get(), 0), total))
        self.btn_copy_fail.config(
            state="normal" if self.log_counts.get("fail") else "disabled")

    def _copy_failures(self):
        """把所有失败行复制到剪贴板，方便贴到记事本里排查。"""
        lines = []
        # tag_ranges 会把**连续**的同 tag 行合并成一个区间（如 2.0→4.0
        # 表示第 2、3 行都是 fail），所以必须按换行拆开，不能只取首行。
        # 另两个坑：ScrolledText 没有 tag_nextr（那是 Text 才有的）；
        # tag_ranges 返回的是 Tcl 对象元组，需先 str() 才能正则解析。
        raw = str(self.log.tag_ranges("fail"))
        nums = [int(x) for x in re.findall(r"(\d+)\.\d+", raw)]
        for lo, hi in zip(nums[0::2], nums[1::2]):
            chunk = self.log.get("%d.0" % lo, "%d.0" % hi)
            for one in chunk.splitlines():
                if one.strip():
                    lines.append(one.rstrip())
        if not lines:
            messagebox.showinfo("没有失败项", "本次没有转换失败的文件。")
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        messagebox.showinfo("已复制", "已复制 %d 条失败记录到剪贴板。" % len(lines))


def _log_fatal(exc):
    """把启动期异常写到桌面旁的日志，便于排查。"""
    try:
        p = os.path.join(tempfile.gettempdir(), "livp_tool_error.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        app = App()
        app._style()
        app.mainloop()
    except Exception:
        _log_fatal(sys.exc_info()[1])
        try:
            messagebox.showerror("启动失败",
                                 "程序启动时出错：\n\n%s\n\n详情见 %s"
                                 % (sys.exc_info()[1],
                                    os.path.join(tempfile.gettempdir(), "livp_tool_error.log")))
        except Exception:
            pass
        raise