# -*- coding: utf-8 -*-
"""生成用于端到端验证的 .livp 测试包（仅开发用，不参与打包）。

用法： python make_test_livp.py <输出目录>
会生成 3 个 .livp：
  IMG_20260315_102333.livp  ->  EXIF 时间 2026-03-15 10:22:33（文件名与 EXIF 一致）
  IMG_20260702_183045.livp  ->  EXIF 时间 2026-07-02 18:30:45
  IMG_1234.livp            ->  无 EXIF、无文件名日期，验证 mtime 兜底
"""
import os
import struct
import subprocess
import sys
import zipfile


def make_jpeg_with_exif(dst_jpg, dt_str):
    """用 ffmpeg 生成 JPEG，再手工插入带 DateTimeOriginal 的 EXIF APP1 段。"""
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=800x600:duration=1",
         "-frames:v", "1", dst_jpg],
        check=True)
    with open(dst_jpg, "rb") as f:
        orig = f.read()

    def le16(v):
        return struct.pack("<H", v)

    def le32(v):
        return struct.pack("<I", v)

    dt = dt_str.encode("ascii") + b"\x00"     # "2026:03:15 10:22:33\0" = 20 bytes
    assert len(dt) == 20, len(dt)
    ifd_start = 8
    # DateTimeOriginal(0x9003) 需要指向 ExifIFD(0x8769)，这里为简化直接写在 IFD0，
    # 真实解析器同时接受 0x0132/0x9003/0x9004，足够验证。
    data_off = ifd_start + 2 + 12 + 4          # 26
    ifd0 = (le16(1) + le16(0x0132) + le16(2) + le32(20) + le32(data_off)
            + le32(0) + dt)
    tiff = b"II*\x00" + le32(ifd_start) + ifd0
    app1 = b"Exif\x00\x00" + tiff
    seg = b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
    with open(dst_jpg, "wb") as f:
        f.write(orig[:2] + seg + orig[2:])    # APP1 必须紧跟 SOI


def build_livp(livp_path, jpg_bytes, mov_bytes):
    with zipfile.ZipFile(livp_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("IMG_0001.jpg", jpg_bytes)
        z.writestr("IMG_0001.mov", mov_bytes)


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else "."
    os.makedirs(outdir, exist_ok=True)

    mov_path = os.path.join(os.path.dirname(os.path.abspath(outdir)), "a.mov")
    with open(mov_path, "rb") as f:
        mov = f.read()

    specs = [
        ("IMG_20260315_102333.livp", "2026:03:15 10:22:33"),
        ("IMG_20260702_183045.livp", "2026:07:02 18:30:45"),
        ("IMG_1234.livp", None),
    ]
    for name, dt in specs:
        tmp = os.path.join(outdir, "_tmp.jpg")
        if dt:
            make_jpeg_with_exif(tmp, dt)
        else:
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "testsrc2=size=800x600:duration=1",
                 "-frames:v", "1", tmp], check=True)
        with open(tmp, "rb") as f:
            jpg = f.read()
        os.remove(tmp)
        build_livp(os.path.join(outdir, name), jpg, mov)
        print("built", name)


if __name__ == "__main__":
    main()