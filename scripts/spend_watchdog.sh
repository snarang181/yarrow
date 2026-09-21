#!/usr/bin/env bash
# Kill every yarrow.driver run the moment metered spend on the current billing
# account reaches the cap. Run detached: nohup scripts/spend_watchdog.sh <since> <cap> &
# Log: runs/spend_watchdog.log
SINCE=${1:-2026-09-14T00:00Z}; CAP=${2:-250}
cd "$(dirname "$0")/.."
LOG=runs/spend_watchdog.log
echo "### watchdog start $(date -u) since=$SINCE cap=$CAP pid=$$" >> "$LOG"
while true; do
  if ! .venv/bin/python scripts/spend_ledger.py --since "$SINCE" --cap "$CAP" --quiet; then
    echo "### CAP REACHED $(date -u) - stopping all yarrow.driver runs" >> "$LOG"
    pkill -f "yarrow[.]driver --system"; sleep 5; pkill -9 -f "yarrow[.]driver --system"
    .venv/bin/python scripts/spend_ledger.py --since "$SINCE" --cap "$CAP" >> "$LOG" 2>&1
    exit 2
  fi
  .venv/bin/python scripts/spend_ledger.py --since "$SINCE" --cap "$CAP" | tail -1 | sed "s/^/$(date -u +%H:%M) /" >> "$LOG"
  sleep 120
done
