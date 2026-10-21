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
import traceback

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText


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
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
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
        """MOV -> GIF，方便直接发微信/社交平台。"""
        vf = ("fps=%d,scale=%d:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse"
              % (fps, width))
        cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
               "-i", mov_path, "-vf", vf, "-loop", "0", out_path]
        rc, log = Converter.run(cmd)
        if rc != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("GIF 生成失败: " + log.strip()[:400])


# ---------------------------------------------------------------- 工作线程

class Worker(threading.Thread):
    """后台线程池：逐个转换，通过 queue 与界面通信。"""

    def __init__(self, jobs, out_root, opts, q):
        super().__init__(daemon=True)
        self.jobs = jobs
        self.out_root = out_root
        self.opts = opts
        self.q = q
        self.stop_flag = threading.Event()

    def stop(self):
        self.stop_flag.set()

    def run(self):
        total = len(self.jobs)
        self.q.put(("total", total))
        ok = fail = skip = 0

        for idx, (src, rel) in enumerate(self.jobs, 1):
            if self.stop_flag.is_set():
                break
            self.q.put(("item", idx, src))
            dst_dir = os.path.join(self.out_root, rel) if rel else self.out_root
            stem = os.path.splitext(os.path.basename(src))[0]

            try:
                os.makedirs(dst_dir, exist_ok=True)
                jpg = os.path.join(dst_dir, stem + ".jpg")
                mp4 = os.path.join(dst_dir, stem + ".mp4")
                gif = os.path.join(dst_dir, stem + ".gif")

                done = []
                work = tempfile.mkdtemp(prefix="livp_")
                try:
                    img, vid = Converter.extract(src, work)

                    if img is None:
                        raise RuntimeError("容器内未找到图片")
                    if self.opts.get("keep_heic"):
                        src_ext = os.path.splitext(img)[1].lower()
                        keep = os.path.join(dst_dir, stem + src_ext)
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
                            keep = os.path.join(dst_dir, stem + ".mov")
                            if not os.path.exists(keep):
                                shutil.copy2(vid, keep)
                            done.append("MOV")
                    if self.stop_flag.is_set():
                        break
                finally:
                    shutil.rmtree(work, ignore_errors=True)

                ok += 1
                self.q.put(("done", idx, src, " + ".join(done)))

            except Exception as e:
                fail += 1
                self.q.put(("fail", idx, src, str(e)))

        self.q.put(("finish", ok, fail, skip, self.stop_flag.is_set()))


# ---------------------------------------------------------------- 界面

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

        self.var_src = tk.StringVar()
        self.var_out = tk.StringVar()
        self.var_preserve = tk.BooleanVar(value=True)
        self.var_mp4 = tk.BooleanVar(value=True)
        self.var_gif = tk.BooleanVar(value=False)
        self.var_keep = tk.BooleanVar(value=False)
        self.var_quality = tk.IntVar(value=2)
        self.var_status = tk.StringVar(value="就绪")
        self.var_count = tk.StringVar(value="尚未添加文件")

        self._build()
        self._pump()
        self._check_env()

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
        self._chk(opts, "输出 GIF 动图", self.var_gif).grid(row=0, column=2, sticky="w")
        self._chk(opts, "同时保留原始 HEIC/MOV", self.var_keep).grid(row=1, column=0, sticky="w", pady=(8, 0))

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

        # --- 进度
        f5 = self._card()
        f5.pack(fill="both", expand=True, padx=pad, pady=(10, pad))
        self._title(f5, "转换日志")
        b5 = f5._body
        self.bar = ttk.Progressbar(b5, mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=(0, 6))
        self.log = ScrolledText(b5, bg=self.CARD, fg=self.FG, font=("Consolas", 9),
                                relief="flat", wrap="none", state="disabled")
        self.log.pack(fill="both", expand=True)
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

    def _chk(self, parent, text, var, state="normal"):
        return tk.Checkbutton(parent, text=text, variable=var, bg=self.CARD, fg=self.FG,
                              activebackground=self.CARD, activeforeground=self.FG,
                              selectcolor=self.CARD, font=("Microsoft YaHei UI", 9), state=state)

    def _update_quality(self, v):
        names = {1: "极高", 2: "很高", 3: "高", 4: "较高", 5: "中", 6: "较低", 7: "低", 8: "很小"}
        self.qual_text.config(text=names.get(int(float(v)), "-"))

    # ---------- 样式
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
        self.var_count.set("待转换：%d 个 .livp 文件" % len(self.files))

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

        opts = {
            "mp4": self.var_mp4.get(),
            "gif": self.var_gif.get(),
            "keep_heic": self.var_keep.get(),
            "quality": self.var_quality.get(),
        }
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        self.write_log("开始转换 %d 个文件 → %s" % (len(self.files), out))
        self.write_log("-" * 58)

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
                elif kind[0] == "item":
                    self.var_status.set("(%d/%d) %s" % (kind[1], self.bar["maximum"],
                                                         os.path.basename(kind[2])))
                elif kind[0] == "done":
                    _, idx, src, made = kind
                    self.bar.config(value=idx)
                    self.write_log("[%d] OK   %s  →  %s" % (idx, os.path.basename(src), made))
                elif kind[0] == "fail":
                    _, idx, src, err = kind
                    self.bar.config(value=idx)
                    self.write_log("[%d] 失败 %s  →  %s" % (idx, os.path.basename(src), err))
                elif kind[0] == "finish":
                    _, ok, fail, _skip, stopped = kind
                    self.write_log("-" * 58)
                    self.write_log("完成：成功 %d，失败 %d%s" % (ok, fail, "（已手动停止）" if stopped else ""))
                    self.var_status.set("已停止" if stopped else "全部完成")
                    self.btn_start.config(state="normal")
                    if ok and not stopped:
                        messagebox.showinfo("转换完成", "成功 %d 个，失败 %d 个。\n\n输出位置：%s"
                                            % (ok, fail, self.var_out.get()))
        except queue.Empty:
            pass
        self.after(120, self._pump)

    def write_log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")


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