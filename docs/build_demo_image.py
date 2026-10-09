#!/usr/bin/env python3
"""Render docs/demo.png from the REAL output of skill-scout.

Nothing here is hand-drawn: the script runs the actual commands, captures their
stdout, trims overlong lines, escapes it all into a terminal-styled HTML page
and screenshots it with headless Chromium. Re-run after changing the tool to
refresh the image.

    python3 docs/build_demo_image.py
"""
from __future__ import annotations

import html
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = Path(__file__).resolve().parent
OUT = DOCS / "demo.png"
SCRIPT = ROOT / "scripts" / "skill_scout.py"

MAX_COLS = 112
MAX_LINES = 30

COMMANDS = [
    ('python3 scripts/skill_scout.py search "video editing"',
     ["search", "video editing"]),
    ('python3 scripts/skill_scout.py inspect vercel-labs/skills@find-skills',
     ["inspect", "vercel-labs/skills@find-skills"]),
]


def capture(args: list[str]) -> str:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, timeout=300, check=False,
    )
    return (r.stdout or r.stderr).rstrip()


def tidy(text: str) -> str:
    """Trim overlong lines so nothing is clipped, and cap the line count."""
    out = []
    for line in text.splitlines()[:MAX_LINES]:
        if len(line) > MAX_COLS:
            line = line[: MAX_COLS - 1].rstrip() + "\u2026"
        out.append(line)
    if text.count("\n") + 1 > MAX_LINES:
        out.append("\u2026")
    return "\n".join(out)


def block(cmd: str, out: str) -> str:
    return (
        '<div class="term">'
        '<div class="bar"><span class="d r"></span><span class="d y"></span>'
        '<span class="d g"></span><span class="t">skill-scout</span></div>'
        f'<pre><span class="p">~ $</span> {html.escape(cmd)}\n'
        f'{html.escape(out)}</pre></div>'
    )


def main() -> int:
    blocks = [block(cmd, tidy(capture(args))) for cmd, args in COMMANDS]

    page = f"""<!doctype html>
<meta charset="utf-8">
<style>
  html,body {{ margin:0; padding:0; }}
  body {{
    background:#0b0e14; width:1080px; padding:30px 28px;
    font-family:"JetBrainsMono Nerd Font","JetBrains Mono",
                "Noto Sans Mono",monospace;
  }}
  h1 {{ color:#e6edf3; font-size:22px; margin:0 0 4px; letter-spacing:-.4px; }}
  h1 small {{ color:#7d8590; font-size:13px; font-weight:400; margin-left:9px; }}
  .lead {{ color:#8b949e; font-size:12.5px; margin:0 0 20px; }}
  .term {{
    background:#0d1117; border:1px solid #232a35; border-radius:11px;
    overflow:hidden; margin-bottom:18px;
    box-shadow:0 12px 34px rgba(0,0,0,.45);
  }}
  .bar {{
    background:#161b22; padding:9px 13px; display:flex;
    align-items:center; gap:7px; border-bottom:1px solid #232a35;
  }}
  .d {{ width:11px; height:11px; border-radius:50%; display:block; }}
  .r {{ background:#ff5f57; }} .y {{ background:#febc2e; }} .g {{ background:#28c840; }}
  .t {{ color:#6e7681; font-size:12px; margin-left:9px; }}
  pre {{
    margin:0; padding:15px 18px; color:#c9d1d9;
    font-size:12.5px; line-height:1.58; white-space:pre; overflow:hidden;
  }}
  .p {{ color:#3fb950; }}
</style>
<h1>skill-scout <small>canonical + security-checked skill discovery</small></h1>
<p class="lead">Real output &mdash; clone/mirror duplicates collapsed, and the
SKILL.md reviewed before anything is installed.</p>
{''.join(blocks)}
"""

    tmp = Path("/tmp/skill-scout-demo.html")
    tmp.write_text(page)

    chrome = next(
        (c for c in ("chromium", "google-chrome-stable", "google-chrome")
         if shutil.which(c)), None
    )
    if not chrome:
        print("no chromium/chrome found", file=sys.stderr)
        return 1

    # generous canvas, then trim to content and re-pad, so nothing is cut off
    subprocess.run(
        [chrome, "--headless", "--no-sandbox", "--disable-gpu",
         "--hide-scrollbars", "--force-device-scale-factor=2",
         "--window-size=1136,2400", f"--screenshot={OUT}", tmp.as_uri()],
        capture_output=True, text=True, timeout=180, check=False,
    )
    if not OUT.exists():
        print("screenshot failed", file=sys.stderr)
        return 1

    if shutil.which("magick"):
        subprocess.run(
            ["magick", str(OUT), "-trim", "+repage",
             "-bordercolor", "#0b0e14", "-border", "56", str(OUT)],
            capture_output=True, text=True, timeout=120, check=False,
        )

    dims = subprocess.run(["file", str(OUT)], capture_output=True, text=True).stdout
    print(f"wrote {OUT} ({OUT.stat().st_size/1024:.0f} KB)")
    print(dims.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
