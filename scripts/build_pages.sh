#!/bin/sh
# The dashboard as a static page for GitHub Pages: app/static plus the run recorded by scripts/record_replay.py,
# answered in the browser with no backend. The Sepolia card reads the live contract from a public endpoint.
#   sh scripts/build_pages.sh [out-dir]      (default: site/)
set -e
out=${1:-site}
rm -rf "$out"
mkdir -p "$out/static"
cp -R app/static/. "$out/static/"
cp results/replay_day.json results/public_chain_day.json "$out/"
sed 's#<script src="static/app.js"></script>#<script>window.SAAT_REPLAY = "replay_day.json";</script><script src="static/app.js"></script>#' app/static/index.html > "$out/index.html"
grep -q SAAT_REPLAY "$out/index.html"
touch "$out/.nojekyll"
echo "built $out/"
