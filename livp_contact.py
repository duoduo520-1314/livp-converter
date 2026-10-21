# -*- coding: utf-8 -*-
"""生成转换结果的 HTML 联系表 —— 用浏览器直接浏览全部照片。

为什么需要：893 张 JPG 散在 17 个相册目录里，挑照片只能一个个开文件夹翻。
联系表把缩略图按相册/日期分组排成一张网页，双击即可浏览，支持点击看大图。

纯标准库实现，不引入任何依赖。缩略图用 ffmpeg 现生成（与转换同一引擎），
单张约 10 KB，893 张的网页体积可控。
"""

import html
import os
import subprocess
import sys

import livp_meta


THUMB_WIDTH = 320

# 与主程序一致：避免生成缩略图时闪出命令行窗口
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def make_thumb(jpg_path, thumb_path, ffmpeg):
    """用 ffmpeg 生成缩略图；失败返回 False。"""
    cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
           "-i", jpg_path,
           "-vf", "scale=%d:-1:flags=lanczos" % THUMB_WIDTH,
           "-frames:v", "1", "-q:v", "6", "-update", "1",
           thumb_path]
    try:
        p = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           creationflags=NO_WINDOW)
        return p.returncode == 0 and os.path.isfile(thumb_path) \
            and os.path.getsize(thumb_path) > 0
    except Exception:
        return False


def scan(out_root):
    """扫描输出目录，返回 [(相册名, [(jpg, mp4或None, 缩略图相对路径)]), ...]。

    按相册（顶层子目录）分组；根目录下的散图归入「未分类」。
    """
    albums = {}
    if not os.path.isdir(out_root):
        return []
    for name in sorted(os.listdir(out_root)):
        p = os.path.join(out_root, name)
        if not os.path.isdir(p) or name.startswith("."):
            continue
        items = []
        for sub, _dirs, files in os.walk(p):
            jpgs = [f for f in files if f.lower().endswith(".jpg")]
            for f in sorted(jpgs):
                stem = os.path.splitext(f)[0]
                mp4 = None
                for ext in (".mp4",):
                    cand = os.path.join(sub, stem + ext)
                    if os.path.isfile(cand):
                        mp4 = cand
                        break
                items.append((os.path.join(sub, f), mp4,
                              os.path.relpath(os.path.join(sub, f), out_root)))
        if items:
            albums[name] = items

    loose = []
    for f in sorted(os.listdir(out_root)):
        p = os.path.join(out_root, f)
        if os.path.isfile(p) and f.lower().endswith(".jpg"):
            mp4 = None
            mp4p = os.path.splitext(p)[0] + ".mp4"
            if os.path.isfile(mp4p):
                mp4 = mp4p
            loose.append((p, mp4, f))
    if loose:
        albums["未分类"] = loose
    return list(albums.items())


_CSS = """
:root{--bg:#0f1115;--card:#181b22;--fg:#e6e6e6;--muted:#9aa0a6;--line:#262a33}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font-family:"Microsoft YaHei UI",-apple-system,"Segoe UI",sans-serif}
header{padding:28px 24px 16px;position:sticky;top:0;background:var(--bg);
 border-bottom:1px solid var(--line);z-index:10}
h1{margin:0 0 6px;font-size:20px;font-weight:500}
.sub{color:var(--muted);font-size:13px}
.bar{margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input[type=search]{background:var(--card);border:1px solid var(--line);
 color:var(--fg);padding:7px 12px;border-radius:8px;font-size:13px;min-width:240px}
button{background:var(--card);border:1px solid var(--line);color:var(--fg);
 padding:7px 12px;border-radius:8px;font-size:13px;cursor:pointer}
button:hover{border-color:#3d4350}
.count{color:var(--muted);font-size:12px;margin-left:auto}
.album{margin:26px 24px}
.album h2{font-size:15px;font-weight:500;margin:0 0 4px}
.album .meta{color:var(--muted);font-size:12px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:12px}
.item{background:var(--card);border:1px solid var(--line);border-radius:10px;
 overflow:hidden;cursor:pointer;transition:transform .12s,border-color .12s}
.item:hover{transform:translateY(-2px);border-color:#3d4350}
.item img{width:100%;display:block;aspect-ratio:1;object-fit:cover;background:#111}
.item .cap{padding:7px 9px;font-size:11px;color:var(--muted);
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.badge{display:inline-block;margin-top:3px;font-size:10px;color:#8ab4f8}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;
 align-items:center;justify-content:center;flex-direction:column;z-index:100;padding:24px}
#lb.on{display:flex}
#lb img{max-width:92vw;max-height:82vh;border-radius:8px}
#lb .t{color:var(--muted);font-size:13px;margin-top:12px;text-align:center}
#lb .c{position:absolute;top:16px;right:22px;color:#fff;font-size:26px;cursor:pointer;background:none;border:none}
#lb .n{position:absolute;top:22px;left:22px;color:#fff;font-size:24px;cursor:pointer;background:none;border:none}
.empty{color:var(--muted);padding:40px 24px;font-size:14px}
"""

_JS = """
const lb=document.getElementById('lb'),li=document.getElementById('limg');
let list=[],cur=0;
function open(i){cur=i;const it=list[i];
  li.src=it.full;document.getElementById('lbt').textContent=it.name+(it.video?'  ·  含视频':'');
  lb.classList.add('on');}
function close(){}
lb.addEventListener('click',e=>{if(e.target.id!=='lb'&&!e.target.closest('.c'))return;
  if(e.target.id==='lb'||e.target.classList.contains('c'))lb.classList.remove('on');});
document.addEventListener('keydown',e=>{
  if(!lb.classList.contains('on'))return;
  if(e.key==='Escape')lb.classList.remove('on');
  if(e.key==='ArrowRight'&&cur<list.length-1)open(cur+1);
  if(e.key==='ArrowLeft'&&cur>0)open(cur-1);});
document.addEventListener('click',e=>{const it=e.target.closest('.item');
  if(it)open(parseInt(it.dataset.i));});
document.getElementById('q').addEventListener('input',function(){
  const v=this.value.toLowerCase();
  document.querySelectorAll('.album').forEach(a=>{
    let n=0;a.querySelectorAll('.item').forEach(it=>{
      const hit=!v||it.dataset.name.toLowerCase().includes(v);
      it.style.display=hit?'':'none';if(hit)n++;});
    a.style.display=n?'':'none';});
  let t=0;document.querySelectorAll('.album').forEach(a=>{if(a.style.display!=='none')t++;});
  document.getElementById('cnt').textContent=t+' 个相册';});
"""


def build(out_root, entries, ffmpeg, thumb_dir=None):
    """生成 index.html。entries 为 scan() 的结果。返回 html 路径。"""
    albums = entries or []
    if not albums:
        return None

    thumb_dir = thumb_dir or os.path.join(out_root, ".thumbs")
    os.makedirs(thumb_dir, exist_ok=True)

    parts = []
    parts.append("<!DOCTYPE html><html lang=\"zh-CN\"><head>"
                 "<meta charset=\"utf-8\">"
                 "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                 "<title>LIVP 转换结果 — 照片联系表</title>"
                 "<style>%s</style></head><body>" % _CSS)

    total = sum(len(v) for _k, v in albums)
    total_video = sum(1 for _k, v in albums for it in v if it[1])
    parts.append("<header><h1>LIVP 转换结果 — 照片联系表</h1>"
                 "<div class=\"sub\">共 %d 张照片 · %d 个相册 · %d 组含视频</div>"
                 "<div class=\"bar\">"
                 "<input id=\"q\" type=\"search\" placeholder=\"按文件名筛选…\">"
                 "<button onclick=\"location.reload()\">刷新</button>"
                 "<span class=\"count\" id=\"cnt\">%d 个相册</span>"
                 "</div></header>" % (total, len(albums), total_video, len(albums)))

    idx = 0
    js_items = []
    for album, items in albums:
        parts.append("<section class=\"album\"><h2>%s</h2>"
                     "<div class=\"meta\">%d 张</div><div class=\"grid\">"
                     % (html.escape(album), len(items)))
        for jpg, mp4, rel in items:
            tpath = os.path.join(thumb_dir, "%s.jpg" % os.path.relpath(jpg, out_root)
                                 .replace(os.sep, "__"))
            tpath = os.path.splitext(tpath)[0] + ".jpg"
            if not os.path.isfile(tpath):
                if not make_thumb(jpg, tpath, ffmpeg):
                    tpath = rel          # 生成失败则退回原图
            name = os.path.splitext(os.path.basename(jpg))[0]
            js_items.append('{"full":%s,"name":%s,"video":%s}'
                            % (json_str(os.path.join(out_root, rel)),
                               json_str(name), "true" if mp4 else "false"))
            parts.append("<div class=\"item\" data-i=\"%d\" data-name=\"%s\">"
                         "<img loading=\"lazy\" src=\"%s\" alt=\"%s\">"
                         "<div class=\"cap\">%s%s</div></div>"
                         % (idx, html.escape(name, quote=True),
                            html.escape(tpath.replace(os.sep, "/"), quote=True),
                            html.escape(name, quote=True),
                            html.escape(name, quote=True),
                            "<span class=\"badge\">▶ 视频</span>" if mp4 else ""))
            idx += 1
        parts.append("</div></section>")

    parts.append("<div id=\"lb\"><button class=\"c\" onclick=\"lb.classList.remove('on')\">×</button>"
                 "<button class=\"n\" onclick=\"if(cur>0)open(cur-1)\">‹</button>"
                 "<img id=\"limg\" alt=\"\"><div class=\"t\" id=\"lbt\"></div></div>")
    parts.append("<script>const LIST=%s;\nlist=LIST;%s</script>"
                 % ("[" + ",".join(js_items) + "]", _JS))
    parts.append("</body></html>")

    path = os.path.join(out_root, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    return path


def json_str(s):
    """最小 JSON 字符串转义。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'