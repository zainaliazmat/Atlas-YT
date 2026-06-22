#!/usr/bin/env bash
# Run videos 2..5 sequentially, each to completion, after any in-flight produce clears.
# One log per video; a final marker when the whole ladder is done.
set -u
cd /home/zain-ali/Documents/YT-AGENTS

wait_for_idle() {
  while pgrep -f "run.py produce" >/dev/null 2>&1; do sleep 15; done
}

declare -A BRIEFS=(
  [v2]="Why is everything online suddenly beige? The rise of the 'sad-beige' internet aesthetic — a 6-scene culture explainer"
  [v3]="Streaming vs cinema: where your \$15 actually goes — a 6-scene data explainer with a clear money breakdown"
  [v4]="Do you really only use 10 percent of your brain? — a 5-scene myth-busting explainer that sets the record straight"
  [v5]="GPT-5 vs Claude vs Gemini: which AI should you actually pay for? — a 7-scene buyer's guide comparing strengths"
)

for v in v2 v3 v4 v5; do
  wait_for_idle
  echo "[$(date +%H:%M:%S)] === STARTING $v ===" >> OWNER_RUN/ladder.log
  bash OWNER_RUN/produce_one.sh "${BRIEFS[$v]}" > "OWNER_RUN/$v.log" 2>&1
  echo "[$(date +%H:%M:%S)] === FINISHED $v ===" >> OWNER_RUN/ladder.log
done
echo "[$(date +%H:%M:%S)] LADDER COMPLETE" >> OWNER_RUN/ladder.log
