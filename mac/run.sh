#!/bin/bash
# Project Karaoke - Mac menu. Run it from wherever this folder is: bash /path/to/mac/run.sh
cd "$(dirname "$0")" || exit 1
here="$(pwd)"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  echo "Installing uv (one time only)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# The Desktop icon: double-click it to sing, no Terminal line needed. It is written here, pointing at this
# folder, rather than shipped as a file: a copied file that arrived by download or cloud sync keeps the
# "downloaded" mark and macOS refuses to open it as possible malware; sync also drops the "runnable" flag.
icon="$HOME/Desktop/Karaoke.command"
want=$(printf '%s\n' '#!/bin/bash' \
  '# Project Karaoke - double-click to sing (written by run.sh). Close the window, or press Return, to stop.' \
  "cd \"$here\" || { echo \"Karaoke folder not found: $here\"; read -r; exit 1; }" \
  'export PATH="$HOME/.local/bin:$PATH"' \
  'uv run player.py')
if [ ! -x "$icon" ] || [ "$(cat "$icon" 2>/dev/null)" != "$want" ] || xattr "$icon" 2>/dev/null | grep -q quarantine; then
  rm -f "$icon"
  printf '%s\n' "$want" > "$icon" && chmod +x "$icon" &&
    echo "Put a Karaoke icon on your Desktop: double-click it next time to start singing."
fi

echo ""
echo "Project Karaoke"
echo "  1) Sing! Play the karaoke from the PC on this Mac (lyrics open in the browser)"
echo "  2) Test: can this Mac run the models? (10-20 min, keep it plugged in)"
echo "  3) Test: sound delay - sound output = MacBook speakers"
echo "  4) Test: sound delay - sound output = the TV"
echo "  5) Test: sound delay - sound output = the TV, TV in Game Mode"
echo "  6) Test: mic delay - picks the fastest clean setting for your voice (~30 s, mic on, pointed at the speakers)"
read -r -p "Type 1-6 and press Return: " n
case "$n" in
  1) uv run player.py ;;
  2) uv run mac_bench.py ;;
  3) uv run tv_latency.py mac-speakers ;;
  4) uv run tv_latency.py tv ;;
  5) uv run tv_latency.py tv-gamemode ;;
  6) uv run mic_latency.py ;;
  *) echo "Nothing picked." ; exit 0 ;;
esac
echo ""
echo "Done. Anything measured is saved in results.txt in this folder."
