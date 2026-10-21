# 贡献指南

感谢参与！这个项目很小，欢迎提 Issue 和 PR。

## 报告问题

请附上：

- Windows 版本
- `.livp` 文件的内部结构（把后缀改成 `.zip` 打开，看看里面有什么）
- 日志区的完整报错信息

## 开发

```bash
pip install -r requirements.txt
python livp_converter.py
```

改动后请自查：

1. 源文件能否正常打开（不报错）
2. 转换结果的**分辨率**是否正常 —— 注意别误取到缩略图
3. 打包后 exe 能否正常启动

```bash
pyinstaller --onefile --windowed --name LIVPConverter livp_converter.py
```

## 代码约定

- 只用标准库，不引入运行时第三方依赖（ffmpeg 是外部程序，不是 pip 包）
- 中文界面，注意 `utf-8` 编码
- 注释说明「为什么」，尤其是 HEIC 多流这类反直觉的处理

## 特别注意

**HEIC 转换时不要加 `-map 0:v:0`。**

HEIC 内部第一个视频流通常是 512×512 缩略图，显式 map 会导出缩略图而非主图。
必须不加 `-map`，让 ffmpeg 自动选流。详见 README「技术说明」。