#!/usr/bin/env python3
"""Scan wallpapers/, write gallery thumbs, and regenerate wallpapers/index.json.

Originals are never resized. Image items get a long-edge ~480px WebP thumb.
Video items are catalogued without a thumb field.

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


def make_thumb(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size > 0:
        return
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
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError as exc:
        raise SyncError("ffmpeg is required to generate wallpaper thumbs") from exc
    except subprocess.CalledProcessError as exc:
        raise SyncError(f"failed to generate thumb for {src.name}") from exc
    if not dest.is_file() or dest.stat().st_size == 0:
        raise SyncError(f"empty thumb written for {src.name}")


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
    if not is_video:
        item["thumb"] = repo_posix(thumb_path_for(src))
    return item


def prune_orphan_thumbs(keep: set[Path]) -> None:
    if not THUMBS.is_dir():
        return
    for path in THUMBS.iterdir():
        if path.is_file() and path not in keep:
            path.unlink()


def main() -> int:
    if not WALLPAPERS.is_dir():
        raise SyncError(f"missing directory: {WALLPAPERS}")

    media = list_media()
    items = []
    keep_thumbs: set[Path] = set()

    for src in media:
        item = catalog_item(src)
        if item["type"] == "image":
            dest = thumb_path_for(src)
            try:
                make_thumb(src, dest)
                keep_thumbs.add(dest)
            except SyncError as exc:
                if dest.is_file() and dest.stat().st_size > 0:
                    keep_thumbs.add(dest)
                    print(f"warning: reused existing thumb for {src.name}: {exc}", file=sys.stderr)
                else:
                    item.pop("thumb", None)
                    print(f"warning: no thumb for {src.name}: {exc}", file=sys.stderr)
        items.append(item)

    prune_orphan_thumbs(keep_thumbs)

    catalog = {
        "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "items": items,
    }
    INDEX.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"indexed {len(items)} items, {len(keep_thumbs)} thumbs -> {repo_posix(INDEX)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
