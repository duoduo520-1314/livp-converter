# -*- coding: utf-8 -*-
"""LIVP 转换工具 V2 —— 图形界面版。

与 V1 共用全部转换逻辑（Converter / Worker / SheetWorker 与各功能模块），
差异只在界面层，见 livp_ui.py 的模块文档。

单独一个入口而不是直接改 V1 的 main：V1 已发布且用户手里有对应版本的 exe，
界面改版应该能并排对比、回退，而不是覆盖掉能跑的版本。
"""

import os
import sys
import tempfile
import traceback


def _bootstrap():
    """打包成 exe 后由 pyinstaller 收集同目录模块；源码直跑时补上路径。"""
    try:
        import livp_converter  # noqa: F401
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


_bootstrap()

from tkinter import messagebox  # noqa: E402

from livp_ui import App  # noqa: E402


def _log_fatal():
    """把启动期异常写到临时目录，便于排查。"""
    try:
        p = os.path.join(tempfile.gettempdir(), "livp_tool_error.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write(traceback.format_exc() + "\n")
        return p
    except Exception:
        return ""


if __name__ == "__main__":
    try:
        app = App()
        app._style()
        app.mainloop()
    except Exception as e:
        p = _log_fatal()
        try:
            messagebox.showerror("启动失败",
                                 "程序启动时出错：\n\n%s\n\n详情见 %s" % (e, p))
        except Exception:
            pass
        raise