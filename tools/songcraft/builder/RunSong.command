#!/bin/bash
# RunSong.command — double-click to build a Manaoke song and WATCH the denmoku
# dashboard tick live. No server: the dashboard is a file:// page that auto-
# refreshes every 4s ONLY while a step is running (render_dashboard.py embeds
# ANY_RUNNING; ↻ in the masthead = manual refresh), and manaoke_build.py
# rewrites it before + after every step. The --auto walk runs the unattended
# steps and stops at any gate (author_data / lyrics / podcast / pitch / deploy
# / promote). Dead simple.

cd "$(dirname "$0")/.." || exit 1     # -> tools/songcraft
SC="$(pwd)"

echo "学オケ · denmoku — run a song"
echo
read -r -p "song key (e.g. odoriko): " KEY
if [ -z "$KEY" ]; then echo "no key — bye."; exit 0; fi

STATE="$SC/builds/${KEY}.build_state.json"
if [ ! -f "$STATE" ]; then
  echo
  echo "No build state for '$KEY' yet — let's init it (blank = skip a field)."
  read -r -p "  --title-jp  : " TJP
  read -r -p "  --title-en  : " TEN
  read -r -p "  --artist    : " ART
  read -r -p "  --artist-en : " AREN
  read -r -p "  --yt (id)   : " YT
  read -r -p "  --apple url : " APPLE
  read -r -p "  --art url (blank = iTunes lookup) : " ARTURL
  python3 "$SC/manaoke_build.py" init "$KEY" \
    --title-jp "$TJP" --title-en "$TEN" --artist "$ART" --artist-en "$AREN" \
    --yt "$YT" --apple "$APPLE" --art "$ARTURL"
fi

# open the auto-refreshing dashboard (file://), then walk the pipeline
python3 "$SC/manaoke_build.py" dash >/dev/null
open "$SC/builder/index.html" 2>/dev/null || true

echo
echo "running: manaoke_build.py run $KEY --auto"
echo "(the dashboard auto-refreshes while a step is running — watch the dots flip)"
echo
python3 "$SC/manaoke_build.py" run "$KEY" --auto

echo
echo "done — dashboard: $SC/builder/index.html"
read -r -p "press Return to close." _
