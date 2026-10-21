# LIVP Converter

把手机拍的**动态照片 / 实况照片**（`.livp`）批量拆分成普通图片和视频。

Windows 11 双击即用，图形界面，无需命令行。

---

## 这是什么问题

用手机拍了「动态照片」，传到电脑 / 发给朋友后，文件后缀变成了 `.livp`。
这类图片**双击打不开**，微信、QQ、邮件、朋友圈也发不了 —— 因为它不是一种通用格式。

`.livp` 实际上是一个 **ZIP 压缩包**，里面装着两个东西：

```
xxx.livp
├── xxx.HEIC   ← 静态照片（主图）
└── xxx.MOV    ← 按下快门前后的那段短视频（约 3 秒，带声音）
```

把它拆开，就得到一张普通 JPG 和一段普通 MP4，任何设备都能用。

## 直接使用（推荐）

1. 到 [Releases](https://github.com/duoduo520-1314/livp-converter/releases) 下载 `LIVPConverter.exe`
2. 双击运行，无需安装
3. 三步搞定：
   - **选择文件**（可多选）或 **选择文件夹**（自动递归查找所有 `.livp`）
   - **选择输出文件夹**
   - 点 **开始转换**

> Windows 首次运行会提示「未知发布者」，点「更多信息 → 仍要运行」即可。

### 需要先装 ffmpeg

本工具用 ffmpeg 做图片和视频转换，**请确保电脑已安装**：

```powershell
winget install Gyan.FFmpeg
```

或从 [ffmpeg.org/download.html](https://ffmpeg.org/download.html) 下载。

也可以直接把 `ffmpeg.exe` 放在本程序同一目录下。启动后日志区会显示「ffmpeg 引擎就绪」即表示成功。

## 功能

| | |
|---|---|
| **选择单个 / 多个文件** | 支持按住 Ctrl / Shift 多选 |
| **选择整个文件夹** | 递归查找，自动保留原有子目录结构 |
| **输出 JPG** | 取全尺寸主图，不是缩略图 |
| **输出 MP4** | 统一转 H.264，任何设备都能播放 |
| **输出 GIF** | 可选，方便发社交平台 |
| **保留原始文件** | 可选，同时保留 HEIC / MOV |
| **画质调节** | 1（最高画质）~ 8（最小体积） |
| **实时进度与日志** | 每个文件的转换结果一目了然 |
| **中途停止** | 随时可以停下来 |

## 支持的文件结构

已实测覆盖以下两种（华为、小米、荣耀等安卓机的动态照片常见）：

- `图片.heic` + `视频.mov`
- `图片.jpeg` + `视频.mov`

程序会自动识别内部文件名，不挑扩展名。

---

## 自行构建

需要 Python 3.8+：

```bash
pip install -r requirements.txt
python livp_converter.py
```

打包成单文件 exe：

```bash
pyinstaller --onefile --windowed --name LIVPConverter livp_converter.py
```

产物在 `dist/LIVPConverter.exe`。

---

## 技术说明

### 为什么不用 `-map 0:v:0`

HEIC 文件内部包含多个视频流，**第一个流通常是 512×512 的缩略图**，
主图排在后面（实测 2316×3088 或 3024×4032）。

如果用常见的 `-map 0:v:0`，导出的是缩略图（30 KB 而非 1.5 MB），画质大打折。
本工具**不加 `-map`**，交给 ffmpeg 自动选择正确的流，才能拿到全尺寸主图。

### 依赖

- Python 3.8+（标准库 `zipfile` + `tkinter`）
- [ffmpeg](https://ffmpeg.org/download.html) —— 运行时从系统 PATH 探测，不打包进 exe

## 常见问题

**Q：提示「未找到 ffmpeg」？**
按上方说明安装 ffmpeg，或把 `ffmpeg.exe` 放到程序同目录。

**Q：HEIC 图片 Windows 打开不了？**
转换输出的是 **JPG**，任何设备都能直接打开，无需额外解码器。

**Q：转换会不会改动我的原文件？**
不会。程序只读取源文件，所有结果写入你指定的输出文件夹。

**Q：处理速度？**
约 1.2 秒/个（同时输出 JPG + MP4）。893 张约 18 分钟。

## 许可证

MIT