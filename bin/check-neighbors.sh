#!/usr/bin/env bash
# Snapshot how the neighbor sites on stonehenge answer, to diff around a VPS change:
#
#   bin/check-neighbors.sh > var/neighbors-before.txt
#   ...bring-up...
#   bin/check-neighbors.sh | diff var/neighbors-before.txt - && echo unchanged
#
# Public requests only; runs from anywhere. Put known Shlink short URLs (the
# ones printed on posters) in NEIGHBOR_EXTRA_URLS, space-separated.
set -uo pipefail

urls=(
  http://mattsinternetsurvivor.com/
  https://mattsinternetsurvivor.com/
  https://growtrellis.co/
  https://www.growtrellis.co/
  https://qr.northerncatskillsmutualaid.com/
  https://shlink.northerncatskillsmutualaid.com/
)
read -ra extra <<< "${NEIGHBOR_EXTRA_URLS:-}"

# ${extra[@]+...}: macOS bash 3.2 treats an empty array as unset under set -u.
for url in "${urls[@]}" ${extra[@]+"${extra[@]}"}; do
  result=$(curl -sS -o /dev/null --max-time 15 -w '%{http_code} %{redirect_url}' "$url" 2>&1)
  echo "$url -> $result"
done
