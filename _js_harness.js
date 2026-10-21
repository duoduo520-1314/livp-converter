// 极简DOM 桩，用于在 node 里直接运行 livp_contact._JS 的真实源码，
// 验证分页数学、筛选、DOM 规模与灯箱索引。所有数量从数据推导，不写死。
const fs = require('fs');
const path = require('path');

function mkEl(id) {
  const el = {
    id, value: '', textContent: '', max: 0, style: {},
    dataset: {}, onclick: null, oninput: null, onchange: null,
    _cards: 0, _heads: 0,
    classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
    insertAdjacentHTML(pos, h) {
      this._cards += (h.match(/class="item"/g) || []).length;
      this._heads += (h.match(/class="ah"/g) || []).length;
    },
    addEventListener() {}, closest() { return null; }, pause() {},
    removeAttribute() {}, getAttribute() { return null; }
  };
  // 真实 DOM 里 innerHTML='' 会清空子节点，桩必须同样重置计数
  let _html = '';
  Object.defineProperty(el, 'innerHTML', {
    get() { return _html; },
    set(v) {
      _html = v;
      if (v === '') { el._cards = 0; el._heads = 0; }
    }
  });
  return el;
}

const els = {};
global.document = {
  getElementById(id) { return els[id] || (els[id] = mkEl(id)); },
  addEventListener() {}, querySelectorAll() { return []; }
};
global.$ = function (id) { return els[id] || (els[id] = mkEl(id)); };
global.window = { scrollTo() {}, scrollY: 0 };
global.IntersectionObserver = function () {
  return { observe() {}, disconnect() {} };
};

const DATA = JSON.parse(fs.readFileSync(path.join(__dirname, '_js_data.json'),
  'utf8'));
const src = fs.readFileSync(path.join(__dirname, '_js_extract.js'), 'utf8');
const TOTAL = DATA.items.length;
const PAGE = 12;                       // 每页 12 张：让 50 张分出多页

const sandbox = { ITEMS: DATA.items, ALBUMS: DATA.albums, CHUNK: DATA.chunk,
                  document, $, IntersectionObserver,
                  Math, String, parseInt, JSON, console };
const runner = new Function(...Object.keys(sandbox),
  src + `
return {
  get view(){return view}, get shown(){return shown},
  get page(){return page}, set page(v){page=v},
  buildView, render, applyFilter, loadMore, photoIndexes, open, step,
  get grid(){return $('grid')},
  get pgInfo(){return $('pgInfo').textContent},
  get lbTitle(){return $('lbt').textContent},
  get sentinel(){return $('sentinel').textContent},
  get cnt(){return $('cnt').textContent},
  get mode(){return $('mode').value}, set mode(v){$('mode').value=v},
  get q(){return $('q').value},   set q(v){$('q').value=v},
  get alb(){return $('alb').value}, set alb(v){$('alb').value=v},
  get size(){return $('size').value}, set size(v){$('size').value=v},
  get lastCard(){return $('grid')._cards},
  resetCards(){const g=$('grid'); g._cards=0; g._heads=0;}
};`);
const A = runner(...Object.values(sandbox));
const ITEMS = DATA.items, ALBUMS = DATA.albums, CHUNK = DATA.chunk;

let fail = 0;
function ok(cond, label, extra) {
  console.log((cond ? '  PASS  ' : '  FAIL  ') + label +
    (extra !== undefined ? '   [' + extra + ']' : ''));
  if (!cond) fail++;
}
const photos = v => v.filter(e => !e.h).length;

console.log('\n=== 数据集 ===');
ok(TOTAL >= 40 && TOTAL <= 60, '照片总数约 50', TOTAL);
ok(ALBUMS.length >= 3 && ALBUMS.length <= 6, '相册数 3-6', ALBUMS.length);
ok(photos(A.view) === TOTAL, '初始 view 含全部照片', photos(A.view));

console.log('\n=== 翻页模式 ===');
A.mode = 'page'; A.size = String(PAGE); A.render();
const pages = Math.ceil(TOTAL / PAGE);
ok(A.lastCard === PAGE, '第 1 页渲染 ' + PAGE + ' 张', A.lastCard);
ok(A.lastCard < TOTAL, 'DOM 卡片数小于总量', A.lastCard + ' / ' + TOTAL);
ok(A.pgInfo.includes('第 1 / ' + pages + ' 页'), '页脚显示页码', A.pgInfo);

A.page = pages - 1; A.render();
const lastN = TOTAL - (pages - 1) * PAGE;
ok(A.lastCard === lastN, '末页张数正确（按张数切页）',
   A.lastCard + ' vs ' + lastN);
ok(A.pgInfo.includes('共 ' + TOTAL + ' 张'), '末页页脚显示总数', A.pgInfo);

A.page = 1; A.render();
ok(A.lastCard === PAGE, '中间页仍为 ' + PAGE + ' 张', A.lastCard);
ok(A.pgInfo.includes('第 2 / '), '页脚跟随切页', A.pgInfo);
ok(A.pgInfo.includes('显示 ' + (PAGE + 1) + '-' + (PAGE * 2)),
   '页脚显示区间正确', A.pgInfo);

A.page = 0; A.size = '7'; A.render();
ok(A.lastCard === 7, '切换每页 7 张生效', A.lastCard);

console.log('\n=== 连续滚动 ===');
A.mode = 'scroll'; A.render();
ok(A.lastCard === Math.min(CHUNK, TOTAL), '首批追加 min(CHUNK,总数) 张',
   A.lastCard);
if (CHUNK < TOTAL) {
  ok(A.sentinel.includes('已加载'), '未载入完时显示进度', A.sentinel);
  let guard = 0;
  while (A.shown < A.view.length && guard++ < 50) A.loadMore();
} else {
  console.log('  SKIP  CHUNK >= 总数，单批即全部载入');
}
ok(A.lastCard === TOTAL, '全部载入后卡片数等于总数', A.lastCard);
ok(A.sentinel.includes('已显示全部'), '末尾提示已全部显示', A.sentinel);
ok(A.grid._heads === ALBUMS.length,
   '相册标题恰好 ' + ALBUMS.length + ' 个（不重复插入）', A.grid._heads);

console.log('\n=== 筛选 ===');
A.mode = 'page'; A.size = String(PAGE); A.render();
const probe = ITEMS[3].n.slice(-3);          // 取真实存在的片段
A.q = probe; A.applyFilter();
const hits = ITEMS.filter(i => i.low.indexOf(probe.toLowerCase()) >= 0).length;
ok(hits > 0, '筛选关键词有命中', probe + ' → ' + hits);
ok(photos(A.view) === hits, '文件名筛选命中数正确',
   photos(A.view) + ' vs ' + hits);
ok(A.lastCard === Math.min(PAGE, hits), '筛选后单页不超过设定', A.lastCard);
A.q = '';

A.q = ''; A.alb = ALBUMS[0].n; A.applyFilter();
ok(photos(A.view) === ALBUMS[0].c, '相册筛选张数正确',
   photos(A.view) + ' vs ' + ALBUMS[0].c);
ok(A.grid._heads === 1, '相册筛选后只剩一个相册标题', A.grid._heads);

A.alb = '不存在的相册'; A.applyFilter();
ok(photos(A.view) === 0, '空相册返回 0 张', photos(A.view));
ok(A.grid.innerHTML.includes('没有匹配'), '空结果显示提示文案', true);

A.alb = ''; A.q = 'zzz不存在'; A.applyFilter();
ok(photos(A.view) === 0, '无匹配关键词返回 0 张', photos(A.view));

console.log('\n=== 灯箱 ===');
A.q = ''; A.applyFilter();
const ids = A.photoIndexes();
ok(ids.length === TOTAL, '灯箱索引覆盖全部照片', ids.length);
// 标题形如 "名字  ·  含视频   3 / 51"，尾部计数用正则取更稳
const pos = () => ((A.lbTitle.match(/(\d+)\s*\/\s*(\d+)\s*$/) || [])[0] || '');
A.open(ids[0]);
ok(pos() === '1 / ' + TOTAL, '灯箱显示 1 / ' + TOTAL, pos());
A.step(1);
ok(pos() === '2 / ' + TOTAL, '右箭头到第 2 张', pos());
A.step(-1); A.step(-1);
ok(pos() === '1 / ' + TOTAL, '左箭头不回越界', pos());
A.open(ids[TOTAL - 1]); A.step(1);
ok(pos() === TOTAL + ' / ' + TOTAL, '末张再右不越界', pos());
const withVid = ITEMS.findIndex(i => i.v);
ok(withVid >= 0, '存在含视频条目走播放分支', withVid);
A.open(withVid);
ok(A.lbTitle.includes('含视频'), '含视频条目标题标注', true);

console.log('\n=== 结果 ===');
console.log(fail === 0 ? '全部通过' : fail + ' 项失败');
process.exit(fail === 0 ? 0 : 1);