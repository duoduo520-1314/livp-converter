# Contributing

Thanks for your interest! This is a small project, and issues and pull requests are very welcome.

## Reporting a bug

Please include:

- Your Windows version
- The internal structure of the `.livp` file (rename it to `.zip`, open it, and describe the contents)
- The complete error message from the log panel

## Development

```bash
pip install -r requirements.txt
python livp_converter.py
```

After making changes, please verify the following:

1. Source files open without errors
2. The **resolution** of the converted output is correct — make sure you have not
   accidentally extracted the embedded thumbnail
3. The packaged executable starts correctly

```bash
pyinstaller --onefile --windowed --name LIVPConverter livp_converter.py
```

## Code conventions

- Use the standard library only. Do not introduce runtime third-party dependencies
  (ffmpeg is an external program, not a pip package)
- The GUI is in English; keep source files UTF-8 encoded
- Comments should explain **why**, especially for counter-intuitive handling such as
  multi-stream HEIC files

## Important

**Never add `-map 0:v:0` when converting HEIC files.**

The first video stream inside a HEIC file is usually a 512×512 thumbnail, so an explicit
`-map` would export the thumbnail instead of the full-resolution still. Omit `-map` and let
ffmpeg select the stream automatically. See "Technical notes" in the README for details.

## Translations

- `README.md` — English (primary)
- `README.zh-CN.md` — Simplified Chinese

When you update one, please keep the other in sync.