# -*- coding: utf-8 -*-
"""生成转换结果的 HTML 联系表 —— 用浏览器直接浏览全部照片。

为什么需要：893 张 JPG 散在 17 个相册目录里，挑照片只能一个个开文件夹翻。
联系表把缩略图按相册/日期分组排成一张网页，双击即可浏览，支持点击看大图。

纯标准库实现，不引入任何依赖。缩略图用 ffmpeg 现生成（与转换同一引擎），
单张约 10 KB，893 张的网页体积可控。

展示层有两种浏览模式，共用同一个切片渲染函数：
  翻页模式   —— 每页固定张数，页脚常驻翻页条。适合逐页挑选。
  连续滚动   —— 分批追加，滚到底自动载入下一批。DOM 里只有已浏览的部分。
两种模式下 DOM 都不会同时存在全部照片，避免 893 张时页面卡死。
"""

import json
import os
import subprocess
import sys


THUMB_WIDTH = 320

# 连续滚动模式每次追加的照片张数
SCROLL_CHUNK = 120

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
:root{--bg:#0f1115;--card:#181b22;--fg:#e6e6e6;--muted:#9aa0a6;--line:#262a33;
 --accent:#8ab4f8}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
 font-family:"Microsoft YaHei UI",-apple-system,"Segoe UI",sans-serif}
header{padding:28px 24px 14px;position:sticky;top:0;background:var(--bg);
 border-bottom:1px solid var(--line);z-index:10}
h1{margin:0 0 6px;font-size:20px;font-weight:500}
.sub{color:var(--muted);font-size:13px}
.bar{margin-top:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input[type=search],select{background:var(--card);border:1px solid var(--line);
 color:var(--fg);padding:7px 12px;border-radius:8px;font-size:13px}
input[type=search]{min-width:220px}
select{max-width:220px}
input[type=number]{width:68px;padding:7px 8px;border-radius:8px;
 background:var(--card);border:1px solid var(--line);color:var(--fg);font-size:13px}
button{background:var(--card);border:1px solid var(--line);color:var(--fg);
 padding:7px 12px;border-radius:8px;font-size:13px;cursor:pointer}
button:hover:not(:disabled){border-color:#3d4350}
button:disabled{opacity:.4;cursor:default}
.count{color:var(--muted);font-size:12px;margin-left:auto}

#grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));
 gap:12px;padding:20px 24px 100px}
.ah{grid-column:1/-1;display:flex;align-items:baseline;gap:10px;
 margin:16px 0 2px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.ah span:first-child{font-size:15px;font-weight:500}
.ahn{color:var(--muted);font-size:12px}
.item{background:var(--card);border:1px solid var(--line);border-radius:10px;
 overflow:hidden;cursor:pointer;transition:transform .12s,border-color .12s}
.item:hover{transform:translateY(-2px);border-color:#3d4350}
.item img{width:100%;display:block;aspect-ratio:1;object-fit:cover;background:#111}
.item .cap{padding:7px 9px;font-size:11px;color:var(--muted);
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.badge{display:inline-block;margin-top:3px;font-size:10px;color:var(--accent)}
#sentinel{text-align:center;color:var(--muted);font-size:12px;
 padding:8px 0 28px;grid-column:1/-1}
.empty{color:var(--muted);padding:70px 24px;font-size:14px;text-align:center;
 grid-column:1/-1}

#pager{position:fixed;left:0;right:0;bottom:0;z-index:20;
 background:rgba(15,17,21,.95);border-top:1px solid var(--line);
 padding:10px 24px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#pager.hidden{display:none}
#pgInfo{color:var(--muted);font-size:12px;margin:0 4px}
#pager .spacer{flex:1}
.hint{color:var(--muted);font-size:12px}

#lb{position:fixed;inset:0;background:rgba(0,0,0,.93);display:none;
 align-items:center;justify-content:center;flex-direction:column;z-index:100;
 padding:64px 24px 24px}
#lb.on{display:flex}
#lb img,#lb video{max-width:92vw;max-height:72vh;border-radius:8px;display:block}
#lb video{background:#000}
#lb .t{color:var(--muted);font-size:13px;margin-top:14px;text-align:center}
#lb button{background:none;border:none;color:#fff;cursor:pointer;padding:4px 12px;
 font-size:26px;line-height:1}
#lb button:hover{color:var(--accent)}
#lb .x{position:absolute;top:14px;right:20px}
#lb .pv{position:absolute;top:16px;left:18px;font-size:30px}
#lb .nx{position:absolute;top:16px;right:64px;font-size:30px}
.lbbtn{position:absolute;bottom:18px;background:var(--card)!important;
 border:1px solid var(--line)!important;color:var(--muted)!important;
 padding:5px 14px!important;border-radius:8px!important;font-size:12px!important}
"""

_JS = r"""
const ITEMS=__ITEMS__, ALBUMS=__ALBUMS__, CHUNK=__CHUNK__;
const $=function(i){return document.getElementById(i)};
const grid=$('grid'), lb=$('lb'), limg=$('limg'), lvid=$('lvid'),
      lbt=$('lbt'), pager=$('pager'), sentinel=$('sentinel');

function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
  .replace(/>/g,'&gt;').replace(/"/g,'&quot;')}

// view  = 渲染节点数组（相册标题与照片交替），筛选后重建
// shown = 已渲染到 view 的位置；分页模式为当页末尾，滚动模式为已加载末尾
let view=[], shown=0, page=0, cur=0, lastAlbum=null;

function buildView(){
  const q=$('q').value.trim().toLowerCase(), al=$('alb').value;
  const hits={}, keep={};
  for(let i=0;i<ITEMS.length;i++){
    const it=ITEMS[i];
    if(al!==''&&it.a!==al) continue;
    if(q&&it.low.indexOf(q)<0) continue;
    hits[it.a]=(hits[it.a]||0)+1; keep[i]=1;
  }
  const out=[];
  for(let k=0;k<ALBUMS.length;k++){
    const a=ALBUMS[k];
    if(!hits[a.n]) continue;
    out.push({h:1,n:a.n,c:hits[a.n]});
    for(let i=a.s;i<a.s+a.c;i++) if(keep[i]) out.push({h:0,i:i});
  }
  return out;
}

function card(i){
  const it=ITEMS[i];
  return '<div class="item" data-v="'+i+'"><img loading="lazy" src="'+esc(it.t)+
    '" alt="'+esc(it.n)+'"><div class="cap">'+esc(it.n)+
    (it.v?'<span class="badge">&#9654; 视频</span>':'')+'</div></div>';
}

// 把 view[from,to) 追加到 DOM。lastAlbum 记录上次渲染的相册，
// 使分批载入时不会在同一相册中间重复插入标题。
function slice(from,to){
  let h='';
  for(let k=from;k<to;k++){
    const e=view[k];
    if(e.h){
      if(e.n!==lastAlbum) h+='<div class="ah"><span>'+esc(e.n)+
        '</span><span class="ahn">'+e.c+' 张</span></div>';
      lastAlbum=e.n;
    }else{
      h+=card(e.i);
    }
  }
  grid.insertAdjacentHTML('beforeend',h);
}

function photoIndexes(){
  const a=[];
  for(let k=0;k<view.length;k++) if(!view[k].h) a.push(view[k].i);
  return a;
}

function updateCount(){
  let n=0;
  for(let k=0;k<view.length;k++) if(!view[k].h) n++;
  $('cnt').textContent=n===ITEMS.length?n+' 张照片'
    :n+' / '+ITEMS.length+' 张照片';
}

function render(){
  grid.innerHTML=''; lastAlbum=null; shown=0;
  const pageMode=$('mode').value==='page';
  pager.classList.toggle('hidden',!pageMode);
  sentinel.style.display=pageMode?'none':'';
  updateCount();
  if(!view.length){
    grid.innerHTML='<div class="empty">没有匹配的照片</div>';
    $('pgInfo').textContent='';
    return;
  }
  if(pageMode){
    const n=parseInt($('size').value,10), total=totalPhotos(),
          pages=Math.max(1,Math.ceil(total/n));
    if(page>=pages) page=pages-1;
    if(page<0) page=0;
    const r=pageRange(page,n);
    slice(r[0],r[1]);
    shown=r[1];
    $('pgInfo').textContent='第 '+(page+1)+' / '+pages+' 页 · 显示 '+
      (page*n+1)+'-'+Math.min(page*n+n,total)+'，共 '+total+' 张';
    $('pgFirst').disabled=$('pgPrev').disabled=(page===0);
    $('pgNext').disabled=$('pgLast').disabled=(page>=pages-1);
    $('jump').max=pages; $('jump').value=page+1;
    window.scrollTo(0,0);
  }else{
    slice(0,Math.min(CHUNK,view.length));
    shown=Math.min(CHUNK,view.length);
    noteProgress();
  }
}

// view 里含相册标题节点，用位置区间算已渲染的照片数，避免每次重算全集
function shownCount(){return view.slice(0,shown).filter(function(e){return !e.h}).length}
function totalPhotos(){let n=0;for(let k=0;k<view.length;k++)if(!view[k].h)n++;return n}

// 把「第 p 页起 n 张照片」换算成 view 的节点区间。
// 按张数而非节点数分页 —— 否则相册标题会占掉名额，
// 出现「每页 120 张」实际只渲染 119 张的偏差。
function pageRange(p,n){
  let seen=0,from=-1,to=-1;
  for(let k=0;k<view.length;k++){
    if(view[k].h) continue;
    if(seen===p*n && from<0) from=k;
    if(seen===p*n+n-1){to=k+1;break;}
    seen++;
  }
  if(from<0){from=view.length;to=view.length;}
  if(to<0) to=view.length;
  // 区间可能从相册中间开始。若紧邻的上一个节点正是该相册的标题，
  // 把它一并纳入，新页面才看得到分组名；否则不回退。
  let s=from;
  if(s>0&&!view[s].h&&view[s-1].h) s--;
  return [s,to];
}

function noteProgress(){
  const all=photoIndexes().length, n=shownCount();
  sentinel.textContent=shown>=view.length?'— 已显示全部 '+n+' 张 —'
    :'已加载 '+n+' / '+all+' 张，继续向下滚动';
}

function loadMore(){
  if(shown>=view.length) return;
  const to=Math.min(shown+CHUNK,view.length);
  slice(shown,to);
  shown=to;
  noteProgress();
}

function applyFilter(){page=0;view=buildView();render()}

// ---------- 灯箱 ----------
function open(i){
  cur=i;
  const it=ITEMS[i];
  if(it.v){lvid.src=it.v; lvid.style.display=''; limg.style.display='none';}
  else{limg.src=it.f; limg.style.display=''; lvid.style.display='none';
       lvid.removeAttribute('src'); lvid.pause();}
  const ids=photoIndexes(), p=ids.indexOf(i);
  lbt.textContent=it.n+(it.v?'  ·  含视频':'')+'   '+(p+1)+' / '+ids.length;
  lb.classList.add('on');
}
function close(){lb.classList.remove('on');lvid.pause();lvid.removeAttribute('src')}
function step(d){
  const ids=photoIndexes(), p=ids.indexOf(cur);
  const np=p+d;
  if(p>=0&&np>=0&&np<ids.length) open(ids[np]);
}

$('x').onclick=close;
$('prev').onclick=function(){step(-1)};
$('next').onclick=function(){step(1)};
lb.onclick=function(e){if(e.target===lb||e.target.id==='x')close()};
document.addEventListener('keydown',function(e){
  if(!lb.classList.contains('on'))return;
  if(e.key==='Escape')close();
  if(e.key==='ArrowRight')step(1);
  if(e.key==='ArrowLeft')step(-1);
});
document.addEventListener('click',function(e){
  const it=e.target.closest('.item');
  if(it) open(parseInt(it.dataset.v,10));
});

// ---------- 控件 ----------
$('q').oninput=applyFilter;
$('alb').onchange=applyFilter;
$('mode').onchange=render;
$('size').onchange=function(){page=0;render()};
$('pgFirst').onclick=function(){page=0;render()};
$('pgPrev').onclick=function(){page--;render()};
$('pgNext').onclick=function(){page++;render()};
$('pgLast').onclick=function(){page=1e9;render()};
$('jump').onchange=function(){page=Math.max(0,(parseInt(this.value,10)||1)-1);render()};

const io=new IntersectionObserver(function(es){
  if(es[0].isIntersecting&&$('mode').value==='scroll') loadMore();
},{rootMargin:'500px'});
io.observe(sentinel);

let opts='<option value="">全部相册（'+ITEMS.length+'）</option>';
for(let k=0;k<ALBUMS.length;k++)
  opts+='<option value="'+esc(ALBUMS[k].n)+'">'+esc(ALBUMS[k].n)+
    '（'+ALBUMS[k].c+'）</option>';
$('alb').innerHTML=opts;
applyFilter();
"""


def build(out_root, entries, ffmpeg, thumb_dir=None):
    """生成 index.html。entries 为 scan() 的结果。返回 html 路径。"""
    albums = entries or []
    if not albums:
        return None

    thumb_dir = thumb_dir or os.path.join(out_root, ".thumbs")
    os.makedirs(thumb_dir, exist_ok=True)

    items = []
    album_stats = []

    for album, its in albums:
        start = len(items)
        for jpg, mp4, rel in its:
            tpath = os.path.join(
                thumb_dir,
                os.path.relpath(jpg, out_root).replace(os.sep, "__"))
            tpath = os.path.splitext(tpath)[0] + ".jpg"
            if not os.path.isfile(tpath):
                if not make_thumb(jpg, tpath, ffmpeg):
                    tpath = rel          # 生成失败则退回原图
            # 一律用相对路径：index.html 与照片同在 out_root 下，
            # 这样整个输出目录可以随意搬移或分享，链接不会失效
            items.append({
                "f": rel.replace(os.sep, "/"),
                "t": os.path.relpath(tpath, out_root).replace(os.sep, "/"),
                "n": os.path.splitext(os.path.basename(jpg))[0],
                "a": album,
                "low": os.path.splitext(os.path.basename(jpg))[0].lower(),
                "v": (os.path.splitext(rel)[0] + ".mp4").replace(os.sep, "/")
                      if mp4 else "",
            })
        album_stats.append({"n": album, "s": start, "c": len(its)})

    total = len(items)
    total_video = sum(1 for it in items if it["v"])

    out = ['<!DOCTYPE html><html lang="zh-CN"><head>'
           '<meta charset="utf-8">'
           '<meta name="viewport" content="width=device-width,initial-scale=1">'
           "<title>LIVP 转换结果 — 照片联系表</title>"
           "<style>" + _CSS + "</style></head><body>"]

    out.append(
        "<header><h1>LIVP 转换结果 — 照片联系表</h1>"
        '<div class="sub">共 %d 张照片 · %d 个相册 · %d 组含视频</div>'
        '<div class="bar">'
        '<input id="q" type="search" placeholder="按文件名筛选…">'
        '<select id="alb" title="按相册筛选"></select>'
        '<select id="mode" title="浏览模式">'
        '<option value="page">翻页浏览</option>'
        '<option value="scroll">连续滚动</option></select>'
        '<select id="size" title="每页张数">'
        '<option value="60">每页 60 张</option>'
        '<option value="120" selected>每页 120 张</option>'
        '<option value="240">每页 240 张</option>'
        '<option value="480">每页 480 张</option></select>'
        '<button onclick="location.reload()">刷新</button>'
        '<span class="count" id="cnt"></span>'
        "</div></header>" % (total, len(albums), total_video))

    out.append('<main id="grid"></main><div id="sentinel"></div>')
    out.append('<div id="pager">'
               '<button id="pgFirst">&#171; 首页</button>'
               '<button id="pgPrev">&#8249; 上一页</button>'
               '<span id="pgInfo"></span>'
               '<button id="pgNext">下一页 &#8250;</button>'
               '<button id="pgLast">末页 &#187;</button>'
               '<span class="spacer"></span>'
               '<span class="hint">跳至</span>'
               '<input id="jump" type="number" min="1" value="1">'
               '<span class="hint">页</span></div>')
    out.append('<div id="lb">'
               '<button class="x" id="x" title="关闭 (Esc)">&times;</button>'
               '<button class="pv" id="prev" title="上一张 (&#8592;)">&#8249;</button>'
               '<button class="nx" id="next" title="下一张 (&#8594;)">&#8250;</button>'
               '<img id="limg" alt="">'
               '<video id="lvid" controls playsinline style="display:none"></video>'
               '<div class="t" id="lbt"></div>'
               '<button class="lbbtn" onclick="close()">关闭 (Esc)</button>'
               "</div>")

    js = (_JS.replace("__ITEMS__", json.dumps(items, ensure_ascii=False))
              .replace("__ALBUMS__", json.dumps(album_stats, ensure_ascii=False))
              .replace("__CHUNK__", str(SCROLL_CHUNK)))
    out.append("<script>" + js + "</script></body></html>")

    path = os.path.join(out_root, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(out))
    return path