"""
Batch convert data/img/ to WebP q=85 with passport dimension resize.

Rules:
- w <= 413 AND h <= 531: keep dimensions, convert to WebP
- w > 413 OR h > 531: proportional resize to fit within 413x531, convert to WebP
- RGBA PNGs: composite onto white background
- CMYK JPEGs: convert to RGB
- Corrupt files: skip, log
- Resume: skip already-converted files
"""

from __future__ import annotations

import time
from pathlib import Path

from PIL import Image

PASSPORT_W = 413
PASSPORT_H = 531
QUALITY = 85
SRC_DIR = Path("data/img")
DST_DIR = Path("data/webp")


def convert(src: Path, dst: Path) -> tuple[bool, str, tuple[int, int] | None]:
    try:
        img = Image.open(src)
    except Exception as e:
        return False, f"open error: {e}", None

    try:
        orig_w, orig_h = img.size

        # Convert color modes
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img.close()
            img = bg
        elif img.mode == "CMYK":
            img = img.convert("RGB")
        elif img.mode == "P":
            img = img.convert("RGB")
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Resize if exceeds passport bounds
        resized = False
        new_w, new_h = orig_w, orig_h
        if orig_w > PASSPORT_W or orig_h > PASSPORT_H:
            ratio = min(PASSPORT_W / orig_w, PASSPORT_H / orig_h)
            new_w = int(orig_w * ratio)
            new_h = int(orig_h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            resized = True

        img.save(dst, "WEBP", quality=QUALITY)
        img.close()
        return True, "ok" if not resized else f"resized {orig_w}x{orig_h} -> {new_w}x{new_h}", (new_w, new_h)
    except Exception as e:
        img.close()
        return False, f"convert error: {e}", None


def main():
    if not SRC_DIR.is_dir():
        print(f"No {SRC_DIR}/ directory — nothing to convert")
        return
    DST_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(
        f for f in SRC_DIR.iterdir() if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
    )

    total = len(files)
    converted = 0
    skipped = 0
    failed = 0
    resized = 0
    total_src_size = 0
    total_dst_size = 0
    failed_files = []

    start = time.time()

    for i, src in enumerate(files, 1):
        dst = DST_DIR / (src.stem + ".webp")

        # Resume: skip if already converted
        if dst.exists():
            skipped += 1
            continue

        ok, msg, dims = convert(src, dst)
        src_sz = src.stat().st_size
        total_src_size += src_sz

        if ok:
            converted += 1
            dst_sz = dst.stat().st_size
            total_dst_size += dst_sz
            if "resized" in msg:
                resized += 1
        else:
            failed += 1
            failed_files.append((src.name, msg))

        # Progress: show current file
        print(f"\r[{i}/{total}] {src.name}  ", end="", flush=True)

    elapsed = time.time() - start
    print()
    print()
    print(f"=== DONE in {int(elapsed // 60)}m{int(elapsed % 60)}s ===")
    print(f"  Total files:   {total}")
    print(f"  Converted:     {converted}")
    print(f"  Skipped:       {skipped} (already existed)")
    print(f"  Failed:        {failed}")
    print(f"  Resized:       {resized}")
    print(f"  Source size:    {total_src_size / 1024 / 1024 / 1024:.2f} GB")
    print(f"  Output size:   {total_dst_size / 1024 / 1024 / 1024:.2f} GB")
    if total_src_size > 0:
        savings = (1 - total_dst_size / total_src_size) * 100
        print(f"  Savings:       {savings:.1f}%")

    if failed_files:
        print()
        print(f"Failed files ({len(failed_files)}):")
        for name, msg in failed_files[:20]:
            print(f"  {name}: {msg}")
        if len(failed_files) > 20:
            print(f"  ... and {len(failed_files) - 20} more")


if __name__ == "__main__":
    main()
