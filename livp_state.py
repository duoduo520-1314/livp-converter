# -*- coding: utf-8 -*-
"""任务状态持久化 —— 支持关闭程序后重新打开接着上次继续。

状态文件位置（按优先级）：
    1. exe 同目录 / 脚本同目录 下的 .livp_state.json
    2. 该目录不可写时，退回 %LOCALAPPDATA%\\LIVPConverter\\state.json

写入采用「临时文件 + 原子替换」，避免转换中途断电/强杀导致状态文件损坏。
"""

import json
import os
import sys
import tempfile
import time

STATE_NAME = ".livp_state.json"
STATE_VERSION = 1


def _writable_dir():
    """找一个可写的目录存放状态文件。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    if os.access(base, os.W_OK):
        return base
    fallback = os.path.join(os.environ.get("LOCALAPPDATA",
                                           tempfile.gettempdir()), "LIVPConverter")
    os.makedirs(fallback, exist_ok=True)
    return fallback


STATE_PATH = os.path.join(_writable_dir(), STATE_NAME)


def _blank():
    return {
        "version": STATE_VERSION,
        "files": [],        # [[绝对路径, 相对目录], ...]
        "out_root": "",
        "src_label": "",
        "opts": {},
        "done": [],         # 已完成的源文件绝对路径
        "updated": 0.0,
    }


def load():
    """读取状态；任何异常都退回空白状态，绝不因状态文件问题影响启动。"""
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION:
            return _blank()
        base = _blank()
        base.update({k: data.get(k, base[k]) for k in base})
        return base
    except Exception:
        return _blank()


def save(state):
    """原子写入。失败只记录不抛出——状态丢失不应中断转换。"""
    try:
        state = dict(state)
        state["version"] = STATE_VERSION
        state["updated"] = time.time()
        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".livp_state_", dir=os.path.dirname(STATE_PATH))
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
            os.replace(tmp_path, STATE_PATH)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return True
    except Exception:
        return False


def clear():
    """删除状态文件（「清除记录」按钮）。"""
    try:
        if os.path.exists(STATE_PATH):
            os.unlink(STATE_PATH)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 续传辅助

def prune_missing(files):
    """剔除源文件已不存在的条目，返回 (有效列表, 失效数量)。"""
    alive, lost = [], 0
    for item in files or []:
        try:
            src = item[0] if isinstance(item, (list, tuple)) else item
        except Exception:
            lost += 1
            continue
        if os.path.isfile(src):
            alive.append(item)
        else:
            lost += 1
    return alive, lost


def remaining(state):
    """尚未完成的文件列表（保留原始 [src, rel] 结构）。"""
    done = set(state.get("done") or [])
    return [it for it in (state.get("files") or [])
            if (it[0] if isinstance(it, (list, tuple)) else it) not in done]


def summarize(state):
    """给界面用的一句话摘要。"""
    files = state.get("files") or []
    done = set(state.get("done") or [])
    return len(files), len([f for f in files if f[0] not in done])