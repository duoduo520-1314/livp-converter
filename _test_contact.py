# -*- coding: utf-8 -*-
"""联系表验证：随机生成 50 张照片的小数据集，验证展示层逻辑。

只用于开发验证，不参与打包。验证分两段：
  1) Python 侧：扫描、缩略图缓存、index.html 体积与占位符替换
  2) JS 侧：导出 _JS 真实源码，由 _js_harness.js 在 node 里跑分页数学

跑法：python _test_contact.py    （可加随机种子参数，默认 7）
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import livp_contact

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(tempfile.gettempdir(), "livp_sheet_test")
TARGET = 50


def make_img(path, color):
    subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=c=%s:s=240x320" % color,
                    "-frames:v", "1", path],
                   creationflags=livp_contact.NO_WINDOW, check=True)


def setup(seed):
    """随机生成约 50 张：相册数、每册张数、是否含视频均随机。"""
    rnd = random.Random(seed)
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT)
    n_albums = rnd.randint(3, 6)
    while True:
        sizes = [rnd.randint(3, 16) for _ in range(n_albums)]
        if TARGET - 8 <= sum(sizes) <= TARGET + 8:
            break
    made = 0
    for i, cnt in enumerate(sizes):
        album = os.path.join(ROOT, "相册 %02d" % (i + 1))
        os.makedirs(album)
        for _ in range(cnt):
            name = "%s-%02d-%02d %06d" % (rnd.choice(["2023", "2024", "2025"]),
                                          rnd.randint(1, 12),
                                          rnd.randint(1, 28),
                                          rnd.randint(0, 999999))
            p = os.path.join(album, name + ".jpg")
            make_img(p, "0x%02x%02x%02x" % (rnd.randrange(256),
                                            rnd.randrange(256),
                                            rnd.randrange(256)))
            if rnd.random() < 0.5:
                open(p[:-4] + ".mp4", "wb").close()   # 占位，测存在性探测
            made += 1
    return made, n_albums, sizes


def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    made, n_alb, sizes = setup(seed)
    print("随机种子 %d：生成 %d 张 / %d 相册，各册 %s"
          % (seed, made, n_alb, sizes))

    entries = livp_contact.scan(ROOT)
    n = sum(len(v) for _k, v in entries)
    nvid = sum(1 for _k, v in entries for it in v if it[1])
    assert n == made, "照片数 %d != %d" % (n, made)
    assert len(entries) == n_alb, "相册数 %d != %d" % (len(entries), n_alb)
    print("scan() 通过：%d 张 / %d 相册，含视频 %d" % (n, len(entries), nvid))

    path = livp_contact.build(ROOT, entries, FFMPEG)
    size = os.path.getsize(path)
    thumbs = len(os.listdir(os.path.join(ROOT, ".thumbs")))
    print("index.html %.1f KB，缩略图 %d 个（首次生成）"
          % (size / 1024.0, thumbs))

    t0 = time.time()
    livp_contact.build(ROOT, entries, FFMPEG)      # 二次应全部命中缓存
    print("二次生成 %.0f ms（缩略图缓存命中）" % ((time.time() - t0) * 1000))

    html = open(path, encoding="utf-8").read()
    for ph in ("__ITEMS__", "__ALBUMS__", "__CHUNK__"):
        assert ph not in html, "占位符未替换：" + ph
    print("模板占位符替换完整")

    # 导出真实 _JS 源码与数据，交给 node harness。
    # 数据从生成的 HTML 里取 —— _JS 模板中它们还是占位符。
    js = re.search(r"<script>(.*)</script>", html, re.S).group(1)
    m_i = re.search(r"const ITEMS=(\[.*?\]), ALBUMS=", js, re.S)
    m_a = re.search(r"ALBUMS=(\[.*?\]), CHUNK=", js, re.S)
    m_c = re.search(r"CHUNK=(\d+);", js)
    assert m_i and m_a and m_c, "未能从 HTML 中解析出 JS 数据"
    # 从 const 声明处开始导出，_JS 前半段（元素引用）也必须在作用域内
    body = js[js.index("const grid="):]
    open(os.path.join(HERE, "_js_extract.js"), "w",
         encoding="utf-8").write(body)
    json.dump({"items": json.loads(m_i.group(1)),
               "albums": json.loads(m_a.group(1)),
               "chunk": int(m_c.group(1))},
              open(os.path.join(HERE, "_js_data.json"), "w",
                   encoding="utf-8"), ensure_ascii=False)
    print("已导出 JS 源码与数据")

    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    r = subprocess.run([node, os.path.join(HERE, "_js_harness.js")],
                       capture_output=True, text=True, encoding="utf-8")
    print(r.stdout)
    if r.stderr.strip():
        print(r.stderr, file=sys.stderr)
    print("index.html 位于:", path)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())