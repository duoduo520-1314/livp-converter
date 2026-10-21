# -*- coding: utf-8 -*-
"""V2 界面 —— 在 V1 逻辑之上重做视觉层。

设计目标（按用户实际痛点排序，不是按「好看」排序）：

1. **修掉 V1 的标题错位**。V1 里 `_card()` 先 pack 了 body，之后才调用
   `_title()`，而 tkinter 的 pack 按调用顺序排列，于是「① 选择要转换的文件」
   这类标题全部渲染在【自己内容的下面】。看起来像三张没有标题的卡片，
   用户不知道每一块是干什么的。
2. **文件列表可见化**。893 个文件选完之后，V1 只显示「待转换：893 个
   .livp 文件」，用户无从确认系统到底找到了什么、有没有漏、是不是选错了
   目录。V2 按相册分组列出数量与体积。
3. **选项区可折叠**。8 个复选框 + 画质滑块长期占据屏幕正中，而绝大多数
   用户只用其中两三个。折叠状态会被记住（否则老用户每次都要重新展开）。
4. **主次动作分层**。V1 把「开始转换」和「清除记录」并排同级，
   误点代价高。V2 把次要动作降级，危险动作移到日志区。
5. **进度给出百分比与计数**，而不是只有一根进度条。

刻意不做的：
- 不换 GUI 框架。PyInstaller 打包体积会从 11 MB 涨到 80 MB+，
  对一个「双击就能用」的小工具是巨大倒退。
- 不做真暗色模式。ttk 的逐控件改色会不协调，Scale 轨道更是改不动，
  半吊子的暗色比浅色更难看。
"""

import os
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import livp_cleanup
import livp_estimate
import livp_state

from livp_converter import App as _AppV1


class Tooltip:
    """极简 tooltip。只有悬停才创建控件，不占布局。"""

    def __init__(self, widget, text):
        self.w = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _show(self, _e=None):
        if self.tip or not self.text:
            return
        x = self.w.winfo_rootx() + 12
        y = self.w.winfo_rooty() + self.w.winfo_height() + 4
        self.tip = tw = tk.Toplevel(self.w)
        tw.wm_overrideredirect(True)
        tw.wm_geometry("+%d+%d" % (x, y))
        f = tk.Frame(tw, bg="#1f2937", bd=0)
        f.pack()
        tk.Label(f, text=self.text, bg="#1f2937", fg="#f9fafb",
                 font=("Microsoft YaHei UI", 8), justify="left",
                 padx=8, pady=5, wraplength=260).pack()

    def _hide(self, _e=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class App(_AppV1):
    """V2 界面。继承 V1 的全部逻辑（消息协议、续传、清理、日志筛选）。"""

    # ---- 设计令牌 ------------------------------------------------
    BG = "#f4f6f9"
    CARD = "#ffffff"
    FG = "#111827"
    MUTED = "#6b7280"
    FAINT = "#9ca3af"
    ACCENT = "#2563eb"
    ACCENT_HOVER = "#1d4ed8"
    BORDER = "#e3e8ef"
    SOFT = "#f8fafc"
    DANGER = "#dc2626"

    OPT_COLLAPSE_KEY = "ui_options_collapsed"

    def __init__(self):
        # 与 V1 相同的初始化顺序，但窗口更大、选项区默认折叠，
        # 因为「选文件 -> 选输出 -> 开始」才是主路径。
        import tkinter as _tk
        _tk.Tk.__init__(self)
        self.title("LIVP 实况照片转换器  v2")
        self.geometry("1000x780")
        self.minsize(940, 700)
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
        self.var_by_date = tk.BooleanVar(value=False)
        self.var_cleanup = tk.BooleanVar(value=False)
        self.var_quality = tk.IntVar(value=2)
        self.var_status = tk.StringVar(value="就绪")
        self.var_count = tk.StringVar(value="尚未添加文件")
        self.var_sum = tk.StringVar()
        self.var_prog = tk.StringVar()
        self.var_sheet = tk.StringVar()
        self.var_est = tk.StringVar()

        self.state = {"files": [], "out_root": "", "src_label": "",
                      "opts": {}, "done": [], "updated": 0.0}
        self.resume_banner = None
        self._album_rows = []

        self._build()
        self._autofit()
        self._bind_estimate()
        self._pump()
        self._check_env()
        self._offer_resume()

    # 窗口外框（标题栏 + 边框）比客户区高的部分。
    # 实测 990 客户区 -> GetWindowRect 1029，即 39 px。
    # 不算这一截，窗口底部就会被屏幕裁掉。
    _FRAME_EXTRA = 40
    # 任务栏等系统占用
    _TASKBAR = 45

    def _autofit(self):
        """按内容实际需要的高度开窗，并保证整个窗口完整落在屏幕内。

        布局里有随相册数量变化的区域（相册清单）与可折叠区，
        固定高度必然出现两种坏结果之一：窗口留一大片空白，
        或者内容被裁掉 —— 日志区实测只剩 2px 高，等于没有。

        第一版只按屏幕高限制了【客户区】高度，结果窗口底部被裁掉
        183px：外框比客户区还高 39px，而窗口又被居中放到 y=234。
        限制必须算上「外框 + 窗口位置」，而且位置要自己定，
        不能交给 tk 居中。
        """
        self.update_idletasks()
        need = self.winfo_reqheight()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()

        y = self._TASKBAR
        # 从 y 出发还能用的客户区高度
        avail = sh - self._TASKBAR - self._FRAME_EXTRA - y
        h = max(560, min(need + 24, avail))
        w = max(940, min(self.winfo_reqwidth() + 60, sw - 60))
        x = max(0, (sw - w) // 2)
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.minsize(900, min(560, avail))

    # ---------- 卡片 ----------
    def _card(self, title, sub=None, right=None, pady=(10, 0), expand=False):
        """白色卡片，直接 pack 到主窗口并返回。

        **标题必须先 pack，再 pack body**：
        tkinter 的 pack 按【调用顺序】排列，先 pack 的在上。
        V1 里 `_card()` 先 pack 了 body、之后才由调用方调 `_title()`，
        于是「① 选择要转换的文件」全部渲染在【自己内容的下面】——
        看起来像三张没有标题的卡片。
        """
        outer = tk.Frame(self, bg=self.CARD, highlightbackground=self.BORDER,
                         highlightthickness=1, bd=0)
        outer.pack(fill="both" if expand else "x", expand=expand,
                   padx=16, pady=pady)

        head = tk.Frame(outer, bg=self.CARD)
        head.pack(fill="x", padx=14, pady=(12, 0))
        tk.Label(head, text=title, bg=self.CARD, fg=self.FG,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        if sub:
            tk.Label(head, text=sub, bg=self.CARD, fg=self.FAINT,
                     font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(8, 0))
        if right is not None:
            right.pack(side="right")

        body = tk.Frame(outer, bg=self.CARD)
        body.pack(fill="both", expand=True, padx=14, pady=(9, 13))
        outer._body = body
        return outer

    def _entry(self, parent, var, readonly=False):
        """带边框的输入框。

        V1 用的是 relief="flat" + 与卡片同色的底，框线几乎看不见，
        用户分不清那是可编辑的输入框还是一段标签文字。这里给一道浅边框。
        """
        return tk.Entry(parent, textvariable=var, bg="#ffffff", fg=self.FG,
                        relief="flat", font=("Microsoft YaHei UI", 9),
                        state="readonly" if readonly else "normal",
                        highlightthickness=1, highlightbackground=self.BORDER,
                        highlightcolor=self.ACCENT, bd=0, insertbackground=self.FG)

    def _btn(self, parent, text, cmd, primary=False, big=False, danger=False):
        style = "Danger.TButton" if danger else ("Accent.TButton" if primary else "Norm.TButton")
        b = ttk.Button(parent, text=text, command=cmd, style=style)
        if big:
            b.configure(padding=(20, 8))
        return b

    def _chk(self, parent, text, var, state="normal", command=None, tip=None):
        c = tk.Checkbutton(parent, text=text, variable=var, bg=self.CARD, fg=self.FG,
                           activebackground=self.CARD, activeforeground=self.FG,
                           selectcolor=self.CARD, font=("Microsoft YaHei UI", 9),
                           state=state, command=command, bd=0, highlightthickness=0)
        if tip:
            Tooltip(c, tip)
        return c

    # ---------- 布局 ----------
    def _build(self):
        pad = 16

        # ===== 顶部标题栏 =====
        head = tk.Frame(self, bg=self.BG)
        head.pack(fill="x", padx=pad, pady=(pad, 0))
        tk.Label(head, text="LIVP 实况照片转换器", bg=self.BG, fg=self.FG,
                 font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        tk.Label(head, text="批量拆分为 JPG 照片 + MP4 视频", bg=self.BG,
                 fg=self.MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(12, 0), pady=(7, 0))
        # 状态胶囊：把「正在做什么」放在视线落点，而不是埋在窗口最底边
        self.lbl_status = tk.Label(head, textvariable=self.var_status, bg=self.ACCENT,
                                   fg="#ffffff", font=("Microsoft YaHei UI", 8, "bold"),
                                   padx=10, pady=3)
        self.lbl_status.pack(side="right")

        # ===== ① 选择文件 + 文件清单 =====
        self.var_albums_on = tk.BooleanVar(value=True)
        f1 = self._card("① 选择要转换的文件")
        self._card_anchor = f1
        b1 = f1._body
        b1.columnconfigure(1, weight=1)

        e1 = self._entry(b1, self.var_src, readonly=True)
        e1.grid(row=0, column=0, columnspan=2, sticky="ew", ipady=5,
                padx=(0, 8), pady=(0, 8))
        self._btn(b1, "选择文件", self.pick_files).grid(row=0, column=2, padx=(0, 6))
        self._btn(b1, "选择文件夹", self.pick_folder, primary=True).grid(row=0, column=3, padx=(0, 6))
        self._btn(b1, "清空", self.clear_files).grid(row=0, column=4)

        # 汇总行：先把「找到了多少、多大」说清楚，再谈别的
        tk.Label(b1, textvariable=self.var_sum, bg=self.CARD, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 8), anchor="w").grid(
            row=1, column=0, columnspan=5, sticky="w")

        # 相册清单（可折叠）：让用户确认系统搜到的到底是什么
        self.lbl_albums_toggle = tk.Button(
            b1, text="▼ 相册明细", command=self._toggle_albums, bg=self.CARD,
            fg=self.ACCENT, relief="flat", bd=0, font=("Microsoft YaHei UI", 8),
            cursor="hand2", anchor="w", activebackground=self.CARD,
            activeforeground=self.ACCENT)
        self.lbl_albums_toggle.grid(row=2, column=0, columnspan=5, sticky="w", pady=(6, 2))

        self.album_wrap = tk.Frame(b1, bg=self.SOFT, highlightbackground=self.BORDER,
                                   highlightthickness=1)
        self.album_wrap.grid(row=3, column=0, columnspan=5, sticky="ew", pady=(0, 2))
        self.album_canvas = tk.Canvas(self.album_wrap, height=96, bg=self.SOFT,
                                      highlightthickness=0, bd=0)
        self.album_sb = tk.Scrollbar(self.album_wrap, orient="vertical",
                                     command=self.album_canvas.yview)
        self.album_inner = tk.Frame(self.album_canvas, bg=self.SOFT)
        self._album_win = self.album_canvas.create_window((0, 0), window=self.album_inner,
                                                          anchor="nw")
        self.album_inner.bind("<Configure>",
                              lambda _e: self.album_canvas.configure(
                                  scrollregion=self.album_canvas.bbox("all")))
        self.album_canvas.bind("<Configure>",
                               lambda e: self.album_canvas.itemconfigure(self._album_win,
                                                                        width=e.width))
        self.album_canvas.configure(yscrollcommand=self.album_sb.set)
        self.album_canvas.pack(side="left", fill="both", expand=True)
        self.album_sb.pack(side="right", fill="y")
        self._albums_visible = True

        # ===== ② 输出位置 =====
        f2 = self._card("② 选择输出位置")
        b2 = f2._body
        b2.columnconfigure(1, weight=1)
        tk.Label(b2, text="输出到", bg=self.CARD, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 9), width=6).grid(row=0, column=0, sticky="w")
        self._entry(b2, self.var_out).grid(row=0, column=1, sticky="ew", ipady=5,
                                             padx=(0, 8), pady=(0, 6))
        self._btn(b2, "浏览", self.pick_out, primary=True).grid(row=0, column=2)
        self._chk(b2, "保留原有子文件夹结构", self.var_preserve).grid(
            row=1, column=0, columnspan=2, sticky="w")
        self._chk(b2, "跳过已转换", self.var_skip,
                  tip="产物已存在就跳过，是断点续传的基础。\n"
                      "重跑一遍 893 个文件只需约 1 秒。").grid(
            row=1, column=2, columnspan=2, sticky="w")

        # ===== ③ 转换选项（可折叠）=====
        self.var_opts_on = tk.BooleanVar(value=False)
        self.btn_opts = tk.Button(
            self, text="▸", command=self._toggle_opts, bg=self.BG, fg=self.MUTED,
            relief="flat", bd=0, font=("Microsoft YaHei UI", 9), cursor="hand2",
            width=2)
        f3 = self._card("③ 转换选项", right=self.btn_opts)
        b3 = f3._body
        b3.columnconfigure(0, weight=1)

        row1 = tk.Frame(b3, bg=self.CARD)
        row1.grid(row=0, column=0, sticky="w")
        self._chk(row1, "JPG 照片（必选）", tk.BooleanVar(value=True), state="disabled").pack(side="left", padx=(0, 18))
        self._chk(row1, "MP4 视频", self.var_mp4).pack(side="left", padx=(0, 18))
        self._chk(row1, "GIF 动图", self.var_gif, command=self._on_gif_toggle,
                  tip="体积很大且生成很慢。\n893 个文件约 5.3 GB、41 分钟。").pack(side="left")

        row2 = tk.Frame(b3, bg=self.CARD)
        row2.grid(row=1, column=0, sticky="w", pady=(7, 0))
        self._chk(row2, "同时保留原始 HEIC/MOV", self.var_keep,
                  tip="额外复制一份原始文件，体积约等于源文件。").pack(side="left", padx=(0, 18))
        self._chk(row2, "按拍摄时间命名", self.var_by_time,
                  command=self._on_by_time_toggle,
                  tip="产物改名为 20260315_102333 这样的时间戳。\n"
                      "时间取自照片 EXIF，其次文件名。").pack(side="left")

        row3 = tk.Frame(b3, bg=self.CARD)
        row3.grid(row=2, column=0, sticky="w", pady=(7, 0))
        self._chk(row3, "按日期分文件夹", self.var_by_date,
                  tip="在相册目录下再分一层 2026-03-15/。\n"
                      "默认关闭：会把已有相册结构打散。").pack(side="left", padx=(0, 18))
        self._chk(row3, "转换后清理源文件（送入回收站）", self.var_cleanup,
                  command=self._on_cleanup_toggle,
                  tip="仅在全部成功后执行，且只清回收站。\n"
                      "清空回收站后无法恢复。").pack(side="left")

        qf = tk.Frame(b3, bg=self.CARD)
        qf.grid(row=3, column=0, sticky="w", pady=(10, 0))
        tk.Label(qf, text="照片质量", bg=self.CARD, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(0, 8))
        self.qual_text = tk.Label(qf, text="很高", bg=self.ACCENT, fg="#ffffff",
                                  font=("Microsoft YaHei UI", 8, "bold"),
                                  padx=8, pady=2, width=5)
        self.qual_text.pack(side="left")
        self.scale = ttk.Scale(qf, from_=1, to=8, orient="horizontal", length=210,
                               variable=self.var_quality, command=self._update_quality)
        self.scale.pack(side="left", padx=(10, 10))
        tk.Label(qf, text="1 = 最高画质（文件大）　　8 = 最小体积", bg=self.CARD,
                 fg=self.FAINT, font=("Microsoft YaHei UI", 8)).pack(side="left")
        self._opts_body = b3
        # 默认折叠：主路径是「选文件 → 选输出 → 开始」，8 个复选框
        # 和画质滑块长期占着屏幕正中却很少被用到。
        # 用 pack_forget 而非 grid_remove —— b3 是被 pack 进卡片的。
        b3.pack_forget()

        # ===== 操作条 =====
        bar = tk.Frame(self, bg=self.CARD, highlightbackground=self.BORDER,
                       highlightthickness=1)
        bar.pack(fill="x", padx=pad, pady=(10, 0))
        tk.Label(bar, textvariable=self.var_count, bg=self.CARD, fg=self.FG,
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="left", padx=(14, 0), pady=11)
        self.btn_start = self._btn(bar, "开始转换", self.start, primary=True, big=True)
        self.btn_start.pack(side="left", padx=(12, 0), pady=8)
        self.btn_stop = self._btn(bar, "停止", self.stop)
        self.btn_stop.pack(side="left", padx=(7, 0), pady=8)
        # 次要动作与危险动作右对齐并降级，误点代价高的不挨着主按钮
        self.btn_clear = self._btn(bar, "清除记录", self.clear_record)
        self.btn_clear.pack(side="right", padx=(0, 6), pady=8)
        self.btn_sheet = self._btn(bar, "生成联系表", self.make_contact_sheet)
        self.btn_sheet.pack(side="right", padx=(0, 6), pady=8)
        self.btn_open = self._btn(bar, "打开输出文件夹", self.open_out)
        self.btn_open.pack(side="right", padx=(0, 6), pady=8)

        # 联系表进度（与转换进度语义不同，独立一条）
        sh = tk.Frame(self, bg=self.CARD, highlightbackground=self.BORDER,
                      highlightthickness=1)
        sh.pack(fill="x", padx=pad, pady=(10, 0))
        self.sheet_bar = ttk.Progressbar(sh, mode="determinate", maximum=100, length=180)
        self.sheet_bar.pack(side="left", padx=(14, 0), pady=9)
        tk.Label(sh, textvariable=self.var_sheet, bg=self.CARD, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(8, 0))
        self.btn_sheet_stop = self._btn(sh, "停止", self.stop_sheet)
        self.btn_sheet_stop.pack(side="right", padx=(0, 12))
        self._sheet_row = sh
        # 用 pack_forget 而不是 grid_remove：sh 是被 pack 管理的，
        # 对它调 grid_remove 是个静默的空操作 —— 整行会一直显示，
        # 留下一个空进度槽和孤零零的「停止」按钮。
        sh.pack_forget()

        # ===== 预估 =====
        est = tk.Label(self, textvariable=self.var_est, bg=self.BG, fg=self.MUTED,
                       font=("Microsoft YaHei UI", 8), anchor="w")
        est.pack(fill="x", padx=pad + 2, pady=(6, 0))
        # 联系表行要靠它钉回原位，见 _show_sheet_row
        self._est_label = est

        # ===== 进度与日志 =====
        # 日志卡要吃掉窗口剩余高度：expand 让它随窗口拉伸，
        # 否则固定高度下窗口一大，日志区就被挤成一条缝
        f4 = self._card("进度与日志", pady=(10, 12), expand=True)
        b4 = f4._body
        prow = tk.Frame(b4, bg=self.CARD)
        prow.pack(fill="x", pady=(0, 5))
        self.bar = ttk.Progressbar(prow, mode="determinate", maximum=100)
        self.bar.pack(side="left", fill="x", expand=True)
        tk.Label(prow, textvariable=self.var_prog, bg=self.CARD, fg=self.MUTED,
                 font=("Microsoft YaHei UI", 8), width=16, anchor="e").pack(side="right", padx=(8, 0))

        flt = tk.Frame(b4, bg=self.CARD)
        flt.pack(fill="x", pady=(0, 5))
        self.var_filter = tk.StringVar(value="all")
        self.log_counts = {"all": 0, "ok": 0, "fail": 0, "skip": 0}
        self.filter_btns = {}
        for key, label in (("all", "全部"), ("ok", "成功"),
                           ("fail", "失败"), ("skip", "跳过")):
            b = tk.Button(flt, text=label, command=lambda k=key: self._set_filter(k),
                          bg=self.CARD, fg=self.FG, relief="flat", bd=0,
                          font=("Microsoft YaHei UI", 9), padx=10, pady=2,
                          cursor="hand2", activebackground=self.SOFT)
            b.pack(side="left")
            self.filter_btns[key] = b
        self.var_filter_label = tk.StringVar(value="尚无记录")
        tk.Label(flt, textvariable=self.var_filter_label, bg=self.CARD, fg=self.FAINT,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(10, 0))
        self.btn_copy_fail = self._btn(flt, "复制失败项", self._copy_failures)
        self.btn_copy_fail.pack(side="right")

        self.log = ScrolledText(b4, bg=self.CARD, fg=self.FG, font=("Consolas", 9),
                                relief="flat", wrap="none", state="disabled")
        self.log.pack(fill="both", expand=True)
        for _tag, _color in self.LOG_TAGS:
            self.log.tag_config(_tag, foreground=_color)
        self._sync_filter_buttons()

    # ---------- 折叠 ----------
    # 两个可折叠区域必须用各自父容器对应的管理方式，否则 tkinter 会报
    # "cannot use geometry manager X inside ... managed by Y"：
    #   · album_wrap 的父容器 b1 内部用 grid -> 必须用 grid/grid_remove
    #   · _opts_body 是卡片 body，被外层用 pack 管理 -> 必须用 pack/pack_forget
    def _toggle_albums(self):
        self._albums_visible = not self._albums_visible
        if self._albums_visible:
            self.album_wrap.grid()
        else:
            self.album_wrap.grid_remove()
        self.lbl_albums_toggle.config(
            text="▼ 相册明细" if self._albums_visible else "▸ 相册明细")

    def _toggle_opts(self):
        show = not self.var_opts_on.get()
        self.var_opts_on.set(show)
        if show:
            self._opts_body.pack(fill="both", expand=True, padx=14, pady=(9, 13))
        else:
            self._opts_body.pack_forget()
        self.btn_opts.config(text="▾" if show else "▸")
        # 折叠状态必须持久化：否则老用户每次启动都要重新展开，
        # 会以为自己的设置丢了
        self.state.setdefault("opts", {})[self.OPT_COLLAPSE_KEY] = not show
        livp_state.save(self.state)

    # ---------- 相册清单 ----------
    def _album_summary(self):
        """按相对目录分组，返回 [(相册名, 张数, 字节数), ...]，按张数倒序。"""
        agg = {}
        for src, rel in self.files:
            key = rel or "（输出根目录）"
            e = agg.setdefault(key, [0, 0])
            e[0] += 1
            try:
                e[1] += os.path.getsize(src)
            except OSError:
                pass
        return [(k, v[0], v[1]) for k, v in sorted(agg.items(), key=lambda kv: -kv[1][0])]

    def _paint_albums(self):
        for w in self._album_rows:
            w.destroy()
        self._album_rows = []
        rows = self._album_summary()
        if not rows:
            tk.Label(self.album_inner, text="尚未添加文件", bg=self.SOFT, fg=self.FAINT,
                     font=("Microsoft YaHei UI", 8), anchor="w",
                     padx=10, pady=8).grid(row=0, column=0, sticky="ew")
            self._album_rows.append(self.album_inner.winfo_children()[-1])
            return
        for i, (name, cnt, size) in enumerate(rows):
            r = tk.Frame(self.album_inner, bg=self.SOFT)
            r.grid(row=i, column=0, sticky="ew", padx=10, pady=1)
            tk.Label(r, text="📁", bg=self.SOFT, font=("Segoe UI Emoji", 8)).pack(side="left")
            tk.Label(r, text=name, bg=self.SOFT, fg=self.FG,
                     font=("Microsoft YaHei UI", 9), anchor="w").pack(
                side="left", fill="x", expand=True, padx=(6, 0))
            tk.Label(r, text="%d 张 · %s" % (cnt, livp_cleanup.fmt_size(size)),
                     bg=self.SOFT, fg=self.MUTED,
                     font=("Microsoft YaHei UI", 8)).pack(side="right")
            self._album_rows.append(r)
        # 高度跟着内容走，但最多 4 行：893 张分 17 个相册时，
        # 全展开会把整个窗口顶出一屏，日志区反而没了
        n = min(len(rows), 4)
        self.album_canvas.configure(height=max(28, n * 23 + 4))
        self.album_canvas.configure(scrollregion=self.album_canvas.bbox("all"))

    def _refresh(self):
        done = set(self.state.get("done") or [])
        todo = len([f for f in self.files if f[0] not in done])
        if todo and todo < len(self.files):
            self.var_count.set("待转换 %d 个（%d 个上次已完成）"
                               % (todo, len(self.files) - todo))
        elif self.files:
            self.var_count.set("待转换 %d 个 .livp 文件" % len(self.files))
        else:
            self.var_count.set("尚未添加文件")

        if self.files:
            total = sum(_s for _n, _c, _s in self._album_summary())
            self.var_sum.set("已找到 %d 个文件，占用 %s，来自 %d 个相册"
                             % (len(self.files), livp_cleanup.fmt_size(total),
                                len(self._album_summary())))
        else:
            self.var_sum.set("")
        self._paint_albums()
        self._refresh_estimate()

    # ---------- 样式 ----------
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Norm.TButton", font=("Microsoft YaHei UI", 9), padding=(12, 5),
                     background="#ffffff", foreground=self.FG,
                     bordercolor=self.BORDER, focusthickness=0)
        st.map("Norm.TButton", background=[("active", self.SOFT)])
        st.configure("Accent.TButton", font=("Microsoft YaHei UI", 9, "bold"),
                     padding=(12, 5), background=self.ACCENT, foreground="#ffffff",
                     bordercolor=self.ACCENT, focusthickness=0)
        st.map("Accent.TButton", background=[("active", self.ACCENT_HOVER),
                                             ("disabled", "#a5b4d4")])
        st.configure("Danger.TButton", font=("Microsoft YaHei UI", 9),
                     padding=(12, 5), background=self.CARD, foreground=self.DANGER,
                     bordercolor="#fecaca", focusthickness=0)
        st.map("Danger.TButton", background=[("active", "#fef2f2")])
        st.configure("TProgressbar", background=self.ACCENT, troughcolor="#eef2f7",
                     bordercolor="#eef2f7", lightcolor=self.ACCENT, darkcolor=self.ACCENT,
                     thickness=8)
        # Scale 的默认轨道是点状虚线，很难看也难拖
        st.configure("Horizontal.TScale", background=self.CARD, troughcolor="#dbe3ee",
                     bordercolor=self.CARD, lightcolor=self.ACCENT, darkcolor=self.ACCENT)

    def _update_quality(self, v):
        names = {1: "极高", 2: "很高", 3: "高", 4: "较高",
                 5: "中", 6: "较低", 7: "低", 8: "很小"}
        self.qual_text.config(text=names.get(int(float(v)), "-"))

    # ---------- 状态胶囊 ----------
    def _set_status(self, text, tone="normal"):
        self.var_status.set(text)
        color = {"normal": self.ACCENT, "busy": "#d97706", "ok": "#15803d",
                 "err": self.DANGER}.get(tone, self.ACCENT)
        self.lbl_status.config(bg=color)

    # ---------- 进度文案 ----------
    def _set_progress_text(self, done=None, total=None, note=""):
        try:
            d = int(self.bar["value"])
            t = int(self.bar["maximum"])
        except Exception:
            d = t = 0
        if t > 0:
            pct = int(d * 100 / t)
            self.var_prog.set("%d%%  %d / %d%s"
                              % (pct, d, t, ("　" + note) if note else ""))
        else:
            self.var_prog.set(note)

    # ================================================================
    # 以下是 V2 相对 V1 的行为差异点。
    # 凡是 V1 已正确的逻辑一律不重复实现，只覆盖需要改观感/交互的地方。
    # ================================================================

    # ---- 恢复折叠状态 ----
    def _offer_resume(self):
        """先恢复「选项区是否折叠」，再走 V1 的续传逻辑。"""
        try:
            st = livp_state.load()
            collapsed = bool((st.get("opts") or {}).get(self.OPT_COLLAPSE_KEY))
        except Exception:
            collapsed = True          # 读不到就沿用本次启动的默认（折叠）
        self.var_opts_on.set(not collapsed)
        self.btn_opts.config(text="▸" if collapsed else "▾")
        if not collapsed:
            self._opts_body.pack(fill="both", expand=True, padx=14, pady=(9, 13))
        _AppV1._offer_resume(self)

    def _persist(self):
        # _toggle_opts 也会调 livp_state.save，若在这里被 _toggle_opts 覆盖就会递归
        _AppV1._persist(self)
        o = self.state.setdefault("opts", {})
        o[self.OPT_COLLAPSE_KEY] = not self.var_opts_on.get()
        livp_state.save(self.state)

    # ---- 状态胶囊随进度变化 ----
    def start(self):
        _AppV1.start(self)
        if self.files and self.var_out.get().strip():
            self._set_status("正在转换…", "busy")
            self._set_progress_text(note="准备中")

    def stop(self):
        _AppV1.stop(self)
        if self.worker and self.worker.is_alive():
            self._set_status("正在停止…", "busy")

    def make_contact_sheet(self):
        _AppV1.make_contact_sheet(self)
        if self.sheet_worker and self.sheet_worker.is_alive():
            self._set_status("正在生成联系表…", "busy")

    # V1 的 _sheet_row 用 grid 管理（它的父容器 b4 内部是 grid），
    # 而 V2 把这一行直接 pack 到主窗口。布局管理方式随父容器而定，
    # 这里必须跟着换，否则 V1 的 grid() 在 V2 里是静默空操作 ——
    # 表现为「点了生成联系表，进度条那一行根本不出现」。
    def _show_sheet_row(self):
        # 必须带 before=：pack_forget 会丢掉原来的 pack 位置，再 pack 就被排到
        # 末尾，落到 expand=True 的日志卡下面。日志卡吃掉了窗口剩余高度，
        # 这一行会被挤出可视区、压成 0 高度 —— 实跑时点了「生成联系表」
        # 却完全看不到进度条（HTML 照样生成，界面毫无反馈），就是这个坑。
        self._sheet_row.pack(fill="x", padx=16, pady=(10, 0),
                             before=self._est_label)

    def _hide_sheet_row(self):
        self._sheet_row.pack_forget()

    def clear_record(self):
        _AppV1.clear_record(self)
        self._paint_albums()

    # ---- 消息派发：只重写需要改观感的那几条，其余原样交给 V1 ----
    def _handle_msg(self, kind):
        k = kind[0]

        if k == "total":
            self.bar.config(maximum=kind[1])
            self._set_progress_text(note="")

        elif k == "item":
            self._set_status("(%d/%d) %s" % (kind[1], self.bar["maximum"],
                                             os.path.basename(kind[2])))

        elif k in ("done", "fail"):
            # 进度条右侧跟着显示当前文件名 —— 长批次时用户需要知道在忙什么
            _AppV1._handle_msg(self, kind)
            self._set_progress_text(note=os.path.basename(kind[2]))

        elif k == "finish":
            _AppV1._handle_msg(self, kind)
            _, ok, fail, skip, stopped = kind[:5]
            if stopped:
                self._set_status("已停止", "busy")
            elif fail:
                self._set_status("完成，有失败", "err")
            else:
                self._set_status("全部完成", "ok")
            self._paint_albums()

        elif k == "progress":
            _AppV1._handle_msg(self, kind)
            self._paint_albums()

        elif k == "sheet_total":
            _, total, miss = kind
            self.sheet_bar.config(maximum=total, value=0)
            self.var_sheet.set("0 / %d（需新建 %d 张）" % (total, miss))

        elif k == "sheet_progress":
            self.sheet_bar.config(value=kind[1])
            self.var_sheet.set("%d / %d　%s"
                               % (kind[1], self.sheet_bar["maximum"], kind[2]))

        else:
            _AppV1._handle_msg(self, kind)