"""Build karaoke/web/look/: the CSS, pictures and fonts of the styles ("looks") the remote page offers.

The looks come from a separate design arsenal (a "design-menu" folder, not part of this repo). Each look
is a recipe: one option id per course (palette, type, shape, texture, depth, ...). The arsenal's CSS keys every
option as .kit[data-<course>="<id>"]; this script keeps only the rules some recipe here uses, copies the pictures
those rules point at, and writes looks.js (recipes + the CSS variables of palette, type and shape).

Rerun after changing RECIPES:  .venv\\Scripts\\python tools\\build_looks.py <path to design-menu>
The output is committed, so a PC without the arsenal never runs this: tune the looks in looks-tune.css instead.
Fonts are fetched separately by tools/fetch_fonts.py (they rarely change)."""
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "karaoke", "web", "look")
if len(sys.argv) < 2:
    sys.exit(__doc__)
MENU = sys.argv[1]
CSS_FILES = ["kit.css", "kit-css1.css", "kit-css2.css", "kit-art.css", "kit-ornate.css", "icons.css", "kit-kits.css",
             "kit-alive.css", "kit-ambient.css", "kit-scene.css", "kit-sig.css"]
# Gallery name -> the id the page stores. Order = order in the menu.
LOOKS = [("neonkroom", "霓虹 K 房"), ("mongkok", "旺角霓虹"), ("wkw", "王家衛的香港電影色彩（王家衛式霓虹懷舊）"),
         ("outrun", "Tron 與 Synthwave / Outrun（電子網格與合成波）"), ("blackcat", "黑貓夜"), ("deco", "Art Deco（裝飾藝術）"),
         ("brass", "木與黃銅"), ("midcentury", "Mid-century Modern（中世紀現代）"), ("qinghua", "青花瓷"),
         ("lofi", "Lo-fi 動畫風（Lo-fi 讀書女孩式）")]
IGNORE = {"dark", "scnfit", "orn", "spot", "s"}   # attributes set at run time, never a recipe course


def recipes():
    """The full recipes and the palette/type/shape variables, read by running the arsenal's own scripts in node."""
    js = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dump_recipes.js")
    out = subprocess.run(["node", js, MENU, json.dumps([n for _, n in LOOKS], ensure_ascii=False)],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


def blocks(css):
    """Top-level (prelude, body) pairs; at-rule bodies stay raw."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    i, n, out = 0, len(css), []
    while i < n:
        j = css.find("{", i)
        if j < 0:
            break
        depth, k = 1, j + 1
        while depth:
            c = css[k]
            depth += c == "{"
            depth -= c == "}"
            k += 1
        out.append((css[i:j].strip(), css[j + 1:k - 1]))
        i = k
    return out


def split_list(sel):
    parts, depth, cur = [], 0, ""
    for c in sel:
        if c == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
            continue
        depth += c == "("
        depth -= c == ")"
        cur += c
    parts.append(cur.strip())
    return parts


ATTR = re.compile(r'\[data-([a-zA-Z-]+)="([^"]*)"\]')
# The icons the pages use (icons.css has ~290 per set; the rest are left out).
ICONS = {"play", "pause", "prev", "next", "shuffle", "stop", "external", "mic", "music", "volume", "sliders", "minus",
         "plus", "settings", "palette", "star", "clock", "keyboard", "phone", "headphones", "speaker", "edit", "check", "warning"}


def attrs(s):
    """[(course, value)] with data-icons-lg read as the course key iconsLg."""
    return [(re.sub(r"-(\w)", lambda m: m.group(1).upper(), k), v) for k, v in ATTR.findall(s)]


def fits(sel, pick):
    """Could this selector match on a page styled with recipe `pick`? :not() is ignored (kept either way)."""
    m = re.search(r"\.ic-([a-z0-9-]+)", sel)
    if m and m.group(1) not in ICONS and "data-icons" in sel:
        return False
    s = re.sub(r":not\((?:[^()]|\([^()]*\))*\)", "", sel)
    for group in re.findall(r":is\(((?:[^()]|\([^()]*\))*)\)", s):
        alts = attrs(group)
        if alts and not any(k in IGNORE or pick.get(k) == v for k, v in alts):
            return False
    s = re.sub(r":is\((?:[^()]|\([^()]*\))*\)", "", s)
    return all(k in IGNORE or pick.get(k) == v for k, v in attrs(s))


def prune(css, picks):
    out = []
    for pre, body in blocks(css):
        if pre.startswith("@media") or pre.startswith("@supports"):
            inner = prune(body, picks)
            if inner:
                out.append(pre + "{" + inner + "}")
        elif pre.startswith("@"):
            out.append(pre + "{" + body + "}")       # keyframes etc.: dropped later if unused
        else:
            keep = [s for s in split_list(pre) if any(fits(s, p) for p in picks)]
            if keep:
                out.append(",".join(keep) + "{" + body.strip() + "}")
    return "\n".join(out)


def drop_unused_keyframes(css):
    names = set(re.findall(r"@keyframes\s+([\w-]+)", css))
    used = {n for n in names if re.search(r"(animation[^;{}]*[\s:,])" + re.escape(n) + r"\b", css)}
    return re.sub(r"@keyframes\s+([\w-]+)\{(?:[^{}]|\{[^{}]*\})*\}", lambda m: m.group(0) if m.group(1) in used else "", css)


def main():
    data = recipes()
    picks = [data["picks"][n] for _, n in LOOKS]
    css = "\n".join(prune(open(os.path.join(MENU, f), encoding="utf-8").read(), picks) for f in CSS_FILES)
    css = drop_unused_keyframes(css)
    os.makedirs(OUT, exist_ok=True)
    for url in sorted(set(re.findall(r"url\((?:\"|')?(assets/[^)\"']+)", css))):
        src, dst = os.path.join(MENU, url), os.path.join(OUT, url)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
    head = "/* Generated by tools/build_looks.py from the Design style arsenal. Do not edit: tune in looks-tune.css. */\n"
    with open(os.path.join(OUT, "kit.css"), "w", encoding="utf-8", newline="\n") as f:
        f.write(head + css + "\n")
    # Noto Sans TC is not shipped (tools/fetch_fonts.py): the device's own CJK sans stands in for it.
    for v in data["vars"].values():
        for k in v:
            v[k] = v[k].replace("'Noto Sans TC'", "'Noto Sans TC','Microsoft JhengHei','PingFang TC'")
    looks = [{"id": i, "name": n, "pick": data["picks"][n], "vars": data["vars"][n], "dark": data["dark"][n],
              "dots": data["dots"][n], "note": data["notes"][n]} for i, n in LOOKS]
    with open(os.path.join(OUT, "looks.js"), "w", encoding="utf-8", newline="\n") as f:
        f.write("/* Generated by tools/build_looks.py. */\nvar LOOKS = " + json.dumps(looks, ensure_ascii=False, indent=1) + ";\n")
    size = sum(os.path.getsize(os.path.join(d, x)) for d, _, fs in os.walk(OUT) for x in fs)
    print(f"kit.css {len(css) // 1024} KB, look/ total {size // 1024} KB")


if __name__ == "__main__":
    main()
