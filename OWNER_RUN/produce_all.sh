#!/usr/bin/env bash
# Run all 5 videos STRICTLY sequentially in one process (no idle-race). Each via the
# produce_one driver; one log per video; a final marker. Wall-clock is the sum.
set -u
cd /home/zain-ali/Documents/YT-AGENTS

run() { # $1=tag  $2=brief
  echo "[$(date +%H:%M:%S)] === STARTING $1 ===" >> OWNER_RUN/ladder.log
  bash OWNER_RUN/produce_one.sh "$2" > "OWNER_RUN/$1.log" 2>&1
  echo "[$(date +%H:%M:%S)] === FINISHED $1 ===" >> OWNER_RUN/ladder.log
}

run v1 "How noise-cancelling headphones actually work — a tight 5-scene explainer with a clear before/after and one big number"
run v2 "Why is everything online suddenly beige? The rise of the 'sad-beige' internet aesthetic — a 6-scene culture explainer"
run v3 "Streaming vs cinema: where your \$15 actually goes — a 6-scene data explainer with a clear money breakdown"
run v4 "Do you really only use 10 percent of your brain? — a 5-scene myth-busting explainer that sets the record straight"
run v5 "GPT-5 vs Claude vs Gemini: which AI should you actually pay for? — a 7-scene buyer's guide comparing strengths"

echo "[$(date +%H:%M:%S)] LADDER COMPLETE" >> OWNER_RUN/ladder.log
