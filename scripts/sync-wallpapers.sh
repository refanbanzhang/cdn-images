#!/bin/bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# Avoid TCC getcwd issues if launchd set a protected cwd
cd "$HOME" || cd /tmp || true

SRC="/Users/refanbanzhang/Desktop/my-space/壁纸"
REPO="/Users/refanbanzhang/Documents/github/cdn-images"
STAGE="${HOME}/Library/Application Support/cdn-images-sync"
LOG_DIR="${HOME}/Documents/cdn-images-sync"
# Prefer logging under Library if Documents log fails
if ! mkdir -p "$LOG_DIR" 2>/dev/null; then
  LOG_DIR="$STAGE"
  mkdir -p "$LOG_DIR"
fi
LOG="${LOG_DIR}/sync.log"
LOCK="${STAGE}/sync.lock"
mkdir -p "$STAGE/wallpapers"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %z')] $*" >> "$LOG" 2>/dev/null || true; echo "$*"; }

sleep 2

if ! mkdir "$LOCK" 2>/dev/null; then
  log "SKIP: another sync is running"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

if [[ ! -d "$REPO/.git" ]]; then log "ERROR: repo missing: $REPO"; exit 1; fi

log "START sync (images + videos) → $REPO"

# Copy source into Library staging (launchd-readable) using osascript when direct access fails
copy_src_to_stage() {
  if ls "$SRC" >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '.DS_Store' --exclude '._*' --exclude 'index.json' --exclude 'thumbs' \
      "$SRC/" "$STAGE/wallpapers/"
    return 0
  fi
  log "Direct Desktop access denied; using osascript staging"
  # Clear staging media then copy via Finder-friendly shell in osascript
  find "$STAGE/wallpapers" -type f ! -name 'index.json' -delete 2>/dev/null || true
  osascript <<OSA
do shell script "rsync -a --delete --exclude '.DS_Store' --exclude '._*' --exclude 'index.json' --exclude 'thumbs' " & quoted form of "$SRC/" & " " & quoted form of "$STAGE/wallpapers/"
OSA
}

copy_src_to_stage || { log "ERROR: staging copy failed"; exit 1; }

# Count staged media
IMG_COUNT=$(find "$STAGE/wallpapers" -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' -o -iname '*.gif' \) | wc -l | tr -d ' ')
VID_COUNT=$(find "$STAGE/wallpapers" -type f \( -iname '*.mp4' -o -iname '*.webm' -o -iname '*.mov' -o -iname '*.m4v' \) | wc -l | tr -d ' ')
log "Staged files: $IMG_COUNT images, $VID_COUNT videos"
if [[ "$IMG_COUNT" -eq 0 && "$VID_COUNT" -eq 0 ]]; then
  log "ERROR: no media staged"
  exit 1
fi

if pgrep -f 'git push.*cdn-images|git-lfs.*cdn-images' >/dev/null 2>&1; then
  log "SKIP: git push/lfs already running"
  exit 0
fi

# Git operations also need Documents access — use osascript wrapper for pull/add/commit/push
export SYNC_REPO="$REPO"
export SYNC_STAGE="$STAGE/wallpapers"
export SYNC_LOG="$LOG"

osascript <<'OSA' || { log "ERROR: git sync via osascript failed"; exit 1; }
do shell script "bash -lc " & quoted form of "
set -euo pipefail
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
REPO=\"$SYNC_REPO\"
STAGE=\"$SYNC_STAGE\"
LOG=\"$SYNC_LOG\"
log() { echo \"[\$(date '+%Y-%m-%d %H:%M:%S %z')] \$*\" >> \"\$LOG\"; echo \"\$*\"; }
cd \"\$REPO\"
git fetch origin
git pull --ff-only origin main
mkdir -p wallpapers
# Preserve gallery thumbs/; sync media from Desktop source
rsync -a --delete --exclude 'index.json' --exclude 'thumbs' \"\$STAGE/\" \"\$REPO/wallpapers/\"
python3 - <<'PY'
import json
from pathlib import Path
from datetime import datetime, timezone
root = Path('wallpapers')
thumbs = root / 'thumbs'
image_ext = {'.jpg','.jpeg','.png','.webp','.gif','.bmp','.heic','.avif'}
video_ext = {'.mp4','.webm','.ogv','.mov','.m4v','.mkv'}
items = []
for p in sorted(root.iterdir(), key=lambda x: x.name):
    if not p.is_file():
        continue
    if p.name.startswith('.') or p.name == 'index.json' or p.name.startswith('._'):
        continue
    ext = p.suffix.lower()
    if ext in image_ext:
        item = {'path': f'wallpapers/{p.name}', 'name': p.name, 'title': p.stem, 'type': 'image', 'ext': ext.lstrip('.'), 'size': p.stat().st_size}
        thumb = thumbs / f'{p.stem}.webp'
        if thumb.is_file():
            item['thumb'] = f'wallpapers/thumbs/{thumb.name}'
        items.append(item)
    elif ext in video_ext:
        items.append({'path': f'wallpapers/{p.name}', 'name': p.name, 'title': p.stem, 'type': 'video', 'ext': ext.lstrip('.'), 'size': p.stat().st_size})
data = {'updatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'items': items}
(root / 'index.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(len(items))
PY
git add wallpapers/
if git diff --cached --quiet; then
  log 'OK: no changes'
  exit 0
fi
MSG=\"sync wallpapers \$(date '+%Y-%m-%d %H:%M:%S %z')\"
git commit -m \"\$MSG\"
git -c http.postBuffer=524288000 push --no-verify origin main
SHA=\$(git rev-parse --short HEAD)
log \"OK: pushed \$SHA — \$MSG\"
"
OSA

log "DONE"
