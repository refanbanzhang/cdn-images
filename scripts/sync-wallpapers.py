#!/usr/bin/env python3
"""Scan wallpapers/, write gallery thumbs, and regenerate wallpapers/index.json.

Originals are never resized. Every image and video item gets a long-edge
~480px WebP thumb. Video thumbs are one decoded frame, same scale/quality
as image thumbs. Existing thumbs are kept; they are never wiped on failure.

Usage (from repo root):
    python3 scripts/sync-wallpapers.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WALLPAPERS = ROOT / "wallpapers"
THUMBS = WALLPAPERS / "thumbs"
INDEX = WALLPAPERS / "index.json"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
VIDEO_EXTS = {".mp4", ".webm", ".ogv", ".mov"}
THUMB_LONG_EDGE = 480
THUMB_QUALITY = "80"
# Long edge capped at 480; -2 keeps even dims for yuv420p WebP.
SCALE_FILTER = (
    f"scale="
    f"'if(gt(iw\\,ih),min({THUMB_LONG_EDGE}\\,iw),-2)':"
    f"'if(gt(iw\\,ih),-2,min({THUMB_LONG_EDGE}\\,ih))'"
)


class SyncError(Exception):
    pass


def repo_posix(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def is_lfs_pointer(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size > 512:
        return False
    try:
        return path.read_bytes().startswith(b"version https://git-lfs.github.com/spec/v1")
    except OSError:
        return False


def is_good_thumb(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0 and not is_lfs_pointer(path)


def list_media() -> list[Path]:
    files = []
    for path in WALLPAPERS.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        ext = path.suffix.lower()
        if ext in IMAGE_EXTS or ext in VIDEO_EXTS:
            files.append(path)
    files.sort(key=lambda p: p.name)
    return files


def thumb_path_for(src: Path) -> Path:
    return THUMBS / f"{src.stem}.webp"


def write_thumb_pillow(src: Path, dest: Path) -> None:
    try:
        from PIL import Image
    except ImportError as exc:
        raise SyncError("ffmpeg libwebp or Pillow is required to generate wallpaper thumbs") from exc

    with Image.open(src) as img:
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
        img.thumbnail((THUMB_LONG_EDGE, THUMB_LONG_EDGE), Image.Resampling.LANCZOS)
        img.save(dest, "WEBP", quality=int(THUMB_QUALITY), method=6)


def write_thumb_ffmpeg(src: Path, dest: Path) -> None:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-frames:v",
        "1",
        "-vf",
        SCALE_FILTER,
        "-c:v",
        "libwebp",
        "-preset",
        "photo",
        "-quality",
        THUMB_QUALITY,
        "-an",
        "-f",
        "webp",
        str(dest),
    ]
    subprocess.run(cmd, check=True)


def make_thumb(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if is_good_thumb(dest):
        return
    if is_lfs_pointer(src):
        raise SyncError(f"LFS pointer not pulled: {src.name}")

    # Sibling tmp keeps the .webp suffix so ffmpeg can pick the muxer.
    tmp = dest.with_name(dest.stem + ".tmp.webp")
    if tmp.exists():
        tmp.unlink()
    is_video = src.suffix.lower() in VIDEO_EXTS
    try:
        try:
            write_thumb_ffmpeg(src, tmp)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            if tmp.exists():
                tmp.unlink()
            if is_video:
                raise SyncError(f"ffmpeg failed to extract video thumb for {src.name}") from exc
            write_thumb_pillow(src, tmp)
        if not is_good_thumb(tmp):
            raise SyncError(f"empty thumb written for {src.name}")
        tmp.replace(dest)
    finally:
        if tmp.exists():
            tmp.unlink()


def catalog_item(src: Path) -> dict:
    ext = src.suffix.lower().lstrip(".")
    is_video = src.suffix.lower() in VIDEO_EXTS
    item = {
        "path": repo_posix(src),
        "name": src.name,
        "title": src.stem,
        "type": "video" if is_video else "image",
        "ext": ext,
        "size": src.stat().st_size,
    }
    item["thumb"] = repo_posix(thumb_path_for(src))
    return item


def prune_orphan_thumbs(keep: set[Path]) -> int:
    """Delete thumbs not in keep. Never wipes the tree when keep is empty."""
    if not THUMBS.is_dir() or not keep:
        return 0
    deleted = 0
    for path in list(THUMBS.iterdir()):
        if not path.is_file() or path.suffix.lower() != ".webp":
            continue
        if path not in keep:
            path.unlink()
            deleted += 1
    return deleted


def main() -> int:
    if not WALLPAPERS.is_dir():
        raise SyncError(f"missing directory: {WALLPAPERS}")

    media = list_media()
    items = []
    keep_thumbs: set[Path] = set()
    failed: list[str] = []

    for src in media:
        item = catalog_item(src)
        dest = thumb_path_for(src)
        try:
            make_thumb(src, dest)
        except SyncError as exc:
            if is_good_thumb(dest):
                print(f"warning: reused existing thumb for {src.name}: {exc}", file=sys.stderr)
            else:
                item.pop("thumb", None)
                failed.append(f"{src.name}: {exc}")
                print(f"warning: no thumb for {src.name}: {exc}", file=sys.stderr)
        if is_good_thumb(dest):
            keep_thumbs.add(dest)
        items.append(item)

    if media and not keep_thumbs:
        raise SyncError(
            f"thumb generation failed for all {len(media)} items; "
            "refusing to prune existing thumbs or rewrite index"
        )

    if failed:
        nfail = len(failed)
        kind = "largely failed" if nfail * 2 >= len(media) else "failed"
        raise SyncError(
            f"thumb generation {kind} ({nfail}/{len(media)}); "
            "skipping prune and index write to avoid committing a wipe. "
            f"first error: {failed[0]}"
        )

    pruned = prune_orphan_thumbs(keep_thumbs)
    if not keep_thumbs and THUMBS.is_dir() and any(THUMBS.iterdir()):
        print("warning: skip prune; keep_thumbs is empty", file=sys.stderr)

    catalog = {
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    INDEX.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"indexed {len(items)} items, {len(keep_thumbs)} thumbs"
        f"{f', pruned {pruned}' if pruned else ''} -> {repo_posix(INDEX)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
