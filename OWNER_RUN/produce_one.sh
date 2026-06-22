#!/usr/bin/env bash
# Drive ONE full production end-to-end: produce -> auto-approve factcheck -> auto-approve
# final_render -> done. Respects the gate flow (a fact-check BLOCK correctly halts and is
# reported, never approved away). Node 22 required for HyperFrames render.
set -u
BRIEF="$1"
cd /home/zain-ali/Documents/YT-AGENTS/atlas
export NVM_DIR="$HOME/.nvm"; . "$NVM_DIR/nvm.sh" >/dev/null 2>&1; nvm use 22 >/dev/null 2>&1
unset ATLAS_RESEARCH_STUB
PY=../venv/bin/python

echo "[$(date +%H:%M:%S)] PRODUCE: $BRIEF"
OUT=$($PY run.py produce "$BRIEF" 2>&1)
echo "$OUT" | tail -3
SLUG=$(ls -t projects/ | head -1)
echo "[$(date +%H:%M:%S)] slug=$SLUG"

status() { $PY -c "import json;print(json.load(open('projects/$SLUG/project.json'))['status'])" 2>/dev/null; }
verdict() { $PY -c "import json;d=json.load(open('projects/$SLUG/project.json'));print(d.get('gates',{}).get('factcheck',{}).get('verdict',''))" 2>/dev/null; }

for i in $(seq 1 8); do
  ST=$(status)
  echo "[$(date +%H:%M:%S)] status=$ST"
  case "$ST" in
    blocked_at_factcheck)
      if [ "$(verdict)" = "block" ]; then
        echo "[$(date +%H:%M:%S)] FACT-CHECK BLOCK (expected for myth-busting) — halting, not approving away."
        break
      fi
      $PY run.py produce "" --resume "$SLUG" --approve factcheck 2>&1 | tail -2 ;;
    blocked_at_final_render)
      $PY run.py produce "" --resume "$SLUG" --approve final_render 2>&1 | tail -2 ;;
    done) echo "[$(date +%H:%M:%S)] DONE"; break ;;
    *) echo "[$(date +%H:%M:%S)] unexpected status '$ST' — stopping"; break ;;
  esac
done

VID="projects/$SLUG/video.mp4"
if [ -f "$VID" ]; then
  SZ=$(du -h "$VID" | cut -f1)
  echo "[$(date +%H:%M:%S)] VIDEO: $VID ($SZ)"
  ffprobe -v error -show_entries format=duration:stream=width,height,codec_name -of default=nw=1 "$VID" 2>/dev/null | head -6
else
  echo "[$(date +%H:%M:%S)] NO video.mp4 (status=$(status))"
fi
echo "[$(date +%H:%M:%S)] END $SLUG"
