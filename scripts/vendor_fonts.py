"""Vendor the Google Fonts used by the frontend into web/vendor/fonts/.

Offline is impossible while the pages <link> to fonts.googleapis.com, so fetch
the woff2 files once and rewrite the @font-face rules to point at them.

Only the latin subset is kept. The UI is English and the only external strings
it renders are Lichess opening names, which stay inside latin-1 ("Réti",
"Grünfeld"); latin-ext would more than double the bundle (Inter's latin-ext
alone is 83 KB per weight against 47 KB for latin).

    python -m scripts.vendor_fonts
"""

import re
from pathlib import Path

import requests

from chess_trainer import PROJECT_ROOT

CSS_URL = ("https://fonts.googleapis.com/css2"
           "?family=Inter:wght@400;500;600;700"
           "&family=Fraunces:ital,wght@0,500;0,600;1,500&display=swap")
# Google serves woff2 only to browsers it recognizes.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
KEEP_SUBSETS = ("latin",)
OUT = PROJECT_ROOT / "web" / "vendor" / "fonts"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    css = requests.get(CSS_URL, headers={"User-Agent": UA}, timeout=30).text

    # Blocks look like: /* latin */\n@font-face {...}
    blocks = re.findall(r"(/\* ([\w-]+) \*/\s*)?(@font-face\s*\{[^}]*\})", css)
    kept, total_bytes = [], 0
    for _, subset, block in blocks:
        if subset and subset not in KEEP_SUBSETS:
            continue
        url = re.search(r"url\((https://[^)]+\.woff2)\)", block)
        if not url:
            continue
        family = re.search(r"font-family:\s*'([^']+)'", block).group(1)
        weight = re.search(r"font-weight:\s*(\d+)", block).group(1)
        style = re.search(r"font-style:\s*(\w+)", block).group(1)
        name = f"{family.lower()}-{weight}{'-italic' if style == 'italic' else ''}-{subset}.woff2"

        data = requests.get(url.group(1), headers={"User-Agent": UA}, timeout=30).content
        (OUT / name).write_bytes(data)
        total_bytes += len(data)
        kept.append(re.sub(r"url\(https://[^)]+\.woff2\)", f"url({name})", block))
        print(f"  {name}  {len(data) / 1024:.1f} KB")

    header = ("/* Vendored from Google Fonts for offline use.\n"
              "   Regenerate with: python -m scripts.vendor_fonts */\n\n")
    (OUT / "fonts.css").write_text(header + "\n".join(kept) + "\n", encoding="utf-8")
    print(f"\nWrote {len(kept)} @font-face rules, {total_bytes / 1024:.0f} KB of woff2 "
          f"to {Path(OUT).relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
