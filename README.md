# LIVP Converter

Batch-extract still images and videos from **Live Photos** (`.livp`) captured on a smartphone.

A native Windows desktop app with a graphical interface — double-click to run, no command line required.

[简体中文](README.zh-CN.md)

---

## The problem

When a Live Photo is transferred to a computer or shared with a friend, the file extension often becomes `.livp`. Such files **cannot be opened by double-clicking**, and cannot be sent through WeChat, QQ, email, or social platforms — because `.livp` is not a widely supported format.

Under the hood, a `.livp` file is simply a **ZIP archive** containing two items:

```
xxx.livp
├── xxx.HEIC   ← the still image
└── xxx.MOV    ← the short clip recorded around the shutter press (~3 seconds, with audio)
```

Extract them and you get a standard JPG image plus a standard MP4 video — both of which work on any device.

## Quick start

1. Download `LIVPConverter.exe` from [Releases](https://github.com/duoduo520-1314/livp-converter/releases)
2. Run it — no installation required
3. Three steps:
   - **Select files** (multiple selection supported) or **select a folder** (recursively finds every `.livp`)
   - **Choose an output folder**
   - Click **Start Conversion**

> On first launch, Windows may display an "Unknown publisher" warning. Click *More info → Run anyway*.

### ffmpeg is required

This tool uses ffmpeg for image and video transcoding, so **ffmpeg must be installed**:

```powershell
winget install Gyan.FFmpeg
```

Or download it from [ffmpeg.org/download.html](https://ffmpeg.org/download.html).

Alternatively, place `ffmpeg.exe` in the same directory as this app. A successful setup is
confirmed by the message `ffmpeg engine ready` in the log panel at startup.

## Features

| Feature | Description |
|---|---|
| **Select files** | Single or multiple selection via Ctrl / Shift |
| **Select a folder** | Recursive scan that preserves the original subfolder structure |
| **Resumable jobs** | Close the app mid-run and reopen it — the file list, output folder, and options are restored so you can continue where you left off |
| **Contact sheet** | Generates a browsable HTML gallery of everything you converted, grouped by album, with paged or continuous-scroll browsing |
| **Timestamp-based naming** | Output files are named `20260315_102333.jpg` using the actual capture time instead of `IMG_1234` |
| **Date-based subfolders** | Optional second-level `2026-03-15/` folders inside each album directory |
| **Skip already converted** | Enabled by default. Files whose output already exists are not reprocessed |
| **JPG output** | Extracts the full-resolution still, not the embedded thumbnail |
| **MP4 output** | Transcoded to H.264 for maximum device compatibility |
| **GIF output** | Optional; a size warning is shown when enabled |
| **Keep originals** | Optionally preserves the source HEIC / MOV files |
| **Quality control** | Adjustable from 1 (highest quality) to 8 (smallest size) |
| **Live progress and logs** | Per-file results reported in real time |
| **Clean up source files** | Optional. Moves `.livp` files to the Recycle Bin after a successful conversion, behind a confirmation prompt |
| **Cancellable** | Stop a running job at any time |

> **Folder structure**: by default your existing album folders (e.g. `2024 Yunnan trip/`)
> are preserved and photos are renamed in place. *Group by date* is **off by default**
> because most people already organise by album, and enabling it would scatter each
> album across dozens of date folders. Turn it on only if your source files have no
> meaningful folder structure.

> **On capture time**: the timestamp is resolved in the following order of precedence, and
> the log labels each file with the source that was actually used.
> 1. **`DateTimeOriginal`** from the image EXIF metadata — the most accurate source
> 2. Inferred from the **file name**, e.g. `IMG_20260315_102333`
>
> If neither yields a time, **the original file name is kept** and the file is counted in
> the summary. The file modification time is deliberately **not** used as a fallback: it is
> routinely rewritten by messaging apps, backups, and bulk renaming. In one measured
> dataset it was off by an average of 54 years, so naming files after it would produce
> plausible-looking but entirely incorrect results.
>
> EXIF inside HEIC files is stored within the container and is not parsed by this tool,
> so those files use the file name instead. Since most smartphone cameras already embed
> a timestamp in the file name, this rarely matters in practice. The `[EXIF]` and
> `[file name]` labels in the log tell you exactly which source was used.
>
> Multiple photos taken within the same second are automatically suffixed with `_2`, `_3`,
> and so on, so nothing is ever overwritten.

> **On GIF**: GIF encoding is slow (~2.8 s per file) and produces large files (~6 MB each).
> A dialog reports the estimated total size when this option is enabled.

## Contact sheet

After converting a large batch, hunting through folders to find the right photo is
tedious. **Generate contact sheet** scans the output folder and builds a single
`index.html` — grouped by album, with thumbnails, a search box, and click-to-enlarge
lightbox (arrow keys to browse). It opens in your default browser and works offline.

Thumbnails are generated with ffmpeg and cached in a `.thumbs` folder, so regenerating the
sheet for an updated batch only processes newly converted photos. For 893 photos the first
run takes roughly 3 minutes and the resulting page is under 1 MB.

### Two browsing modes

Neither mode ever puts every photo into the DOM at once, which is what makes a sheet of
893 photos (or 10,000) stay responsive.

| Mode | Behaviour | Best for |
|---|---|---|
| **Paged** (default) | Renders a fixed number of photos per page — 60, 120, 240 or 480. A pager sits at the bottom with first / previous / next / last and a jump-to-page box. | Picking through a batch page by page |
| **Continuous scroll** | Appends the next batch automatically as you reach the bottom. | Skimming the whole set in one pass |

Photos are **counted, not nodes**: album headers never consume a slot, so "120 per page"
always means exactly 120 photos. If a page happens to begin in the middle of an album, its
header is repeated so you never lose track of where you are.

### Filtering

- **Search** matches against the file name, as you type.
- **Album** narrows the sheet to a single album.

Both filters apply in **either** mode, and the lightbox navigates only the photos that
matched — arrow keys will never jump to a photo you filtered out.

### Lightbox

Click any thumbnail to enlarge it. Arrow keys move through the current result set, `Esc`
closes. Photos that have a matching MP4 open in a video player with playback controls
instead of a still image.

> All links in the sheet are **relative**, so the output folder can be moved, renamed or
> shared as-is — the page keeps working as long as it stays next to the photos.

## Cleaning up source files (use with care)

When **Clean up source .livp** is enabled, source files are moved to the **Recycle Bin**
after all conversions succeed. Enabling it shows a risk warning and requires confirmation.

**Three things to keep in mind:**

1. **The Recycle Bin does not free space.** Files stay on disk until you empty it yourself.
2. **Emptying the Recycle Bin is irreversible.** The `.livp` is the only remaining source
   for the original HEIC image and video.
3. **JPG is lossy.** At higher quality settings you may regret the conversion later.

**Built-in safeguards:**

- Cleanup runs **only after every file converts successfully**
- If you stop the job or **any** file fails, **not a single source file is deleted**
- Each JPG (and MP4) is verified to exist and be non-empty before its source is deleted
- If the Recycle Bin is full, source files are kept and the error is reported — never a
  silent failure
- This option is **not restored from the job record**; it must be re-confirmed each time
- The checkbox resets after a run so it cannot affect the next batch

To recover files, simply restore them from the Recycle Bin.

## Resuming an interrupted job

1. Add your files, choose an output folder, and click **Start Conversion**
2. You may close the app or click **Stop** at any point during the run
3. On the next launch, a banner appears at the top of the window summarising how many
   files were in the previous job, how many completed, and how many remain
4. Click **Start Conversion** again — completed files are skipped automatically and only
   the remaining ones are processed

The job record is stored in `.livp_state.json` next to the executable (falling back to
`%LOCALAPPDATA%\LIVPConverter\` if that directory is not writable). Use the **Clear record**
button to delete it — this never affects any already-converted files.

> Adding a different set of source files or a different output folder simply overwrites the
> previous record. Use **Clear record** to start from scratch.

## Supported internal structures

Both of the following layouts have been verified against real devices (common on Android
phones from Huawei, Xiaomi, HONOR, and others):

- `image.heic` + `video.mov`
- `image.jpeg` + `video.mov`

Internal file names are detected automatically; the extension is not assumed.

---

## Building from source

Requires Python 3.8+:

```bash
pip install -r requirements.txt
python livp_converter.py
```

To build a single-file executable:

```bash
pyinstaller --onefile --windowed --name LIVPConverter livp_converter.py
```

The artifact is written to `dist/LIVPConverter.exe`.

---

## Technical notes

### Why `-map 0:v:0` is never used

A HEIC file contains multiple video streams, and **the first stream is usually a 512×512
thumbnail**. The full-resolution still appears later in the file (measured at 2316×3088 or
3024×4032 on real devices).

The commonly suggested `-map 0:v:0` would therefore export the thumbnail — roughly 30 KB
instead of 1.5 MB — producing a significant loss of image quality.

This tool **omits `-map` entirely** and lets ffmpeg select the correct stream automatically,
which yields the full-resolution image.

### Dependencies

- Python 3.8+ (standard library only: `zipfile` + `tkinter`)
- [ffmpeg](https://ffmpeg.org/download.html) — discovered from the system `PATH` at runtime
  and deliberately **not** bundled into the executable

Capture-time resolution is likewise implemented with the **standard library alone**
(`livp_meta.py` contains a hand-written EXIF/TIFF parser). No dependency such as Pillow is
introduced, so the executable size is unaffected.

### Project structure

| File | Responsibility |
|---|---|
| `livp_converter.py` | Main application: conversion logic, background worker, GUI |
| `livp_meta.py` | Capture-time resolution: EXIF → file name, with no unsafe guessing |
| `livp_state.py` | Job state persistence backing resumable jobs (atomic writes, versioned so stale option defaults are not restored) |
| `livp_contact.py` | Contact-sheet generation: thumbnail cache and HTML gallery, with paged and continuous-scroll rendering over a shared slice renderer |
| `livp_cleanup.py` | Optional source-file cleanup via the Windows Recycle Bin (ctypes `SHFileOperationW`, no permanent delete) |
| `make_test_livp.py` | Development helper that generates test `.livp` files; not part of the build |

## FAQ

**Q: "ffmpeg not found" on startup?**
Install ffmpeg as described above, or place `ffmpeg.exe` next to the application.

**Q: Windows can't open the HEIC images?**
The tool outputs **JPG**, which every device opens natively — no additional codec required.

**Q: Does the conversion modify my original files?**
No. Source files are opened read-only and every artifact is written to the output folder
you choose.

**Q: How fast is it?**
About 1.2 seconds per file when producing both JPG and MP4. A batch of 893 files takes
roughly 18 minutes.

**Q: Will this read the capture time from my HEIC photos?**
Not directly — HEIC EXIF is not parsed. Files whose names contain a timestamp
(`IMG_20260315_102333`) are handled correctly. For anything else the original file name
is kept rather than guessing, and the summary reports how many files were affected.

**Q: Why aren't my photos grouped into date folders by default?**
Because album folders such as `2024 Yunnan trip/` are usually more meaningful than
`2024-07-02/`, and date grouping would scatter them. The option exists and is off by
default — enable it if your sources have no meaningful folder structure.

## License

MIT