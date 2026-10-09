#!/usr/bin/env python3
"""
skill-scout — clean, canonical, security-checked discovery for the agent-skills registry.

It wraps `npx skills find` and fixes two problems in the raw registry output:

  1. CLONE POLLUTION — the same skill is republished under many owners, and each
     copy carries a duplicated install count. A skill shown with "710K installs"
     can come from a 5-star mirror while the original (296K stars) is buried.
     skill-scout groups results by skill name and resolves the canonical
     (original, non-forked) repository, then reports how many clones it suppressed.

  2. NO SECURITY REVIEW — installing a skill means running third-party
     instructions with full agent permissions. skill-scout reads the SKILL.md and
     flags shell execution, credential access, exfiltration and prompt-injection
     patterns before you install anything.

Usage:
    skill_scout.py search "video editing" [--owner vercel-labs] [--json] [--limit N]
    skill_scout.py inspect <owner/repo@skill> [--json]
    skill_scout.py domains                     # curated domain bundles

Auth: set GITHUB_TOKEN (or GH_TOKEN) to raise the GitHub API rate limit from
60 to 5000 req/hour. Results are cached under ~/.cache/skill-scout for 24h.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CACHE_DIR = Path(os.environ.get("SKILL_SCOUT_CACHE", Path.home() / ".cache" / "skill-scout"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
REPO_CACHE = CACHE_DIR / "repos.json"
BLOB_CACHE = CACHE_DIR / "blobs.json"
CACHE_TTL = 24 * 3600

# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data))
    except Exception:
        pass


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def gh_api(url: str) -> dict:
    """GET a GitHub API URL. Returns {'error': msg} on failure."""
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "skill-scout")
    tok = _token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0":
            return {"error": "rate-limited"}
        if e.code == 404:
            return {"error": "not-found"}
        return {"error": f"http-{e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def raw_get(url: str, max_bytes: int = 512_000) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": "skill-scout"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read(max_bytes).decode("utf-8", "replace")
    except Exception:
        return None


def repo_meta(full_name: str, cache: dict) -> dict:
    """Cached repo metadata: stars, fork, parent, pushed_at, archived."""
    entry = cache.get(full_name)
    if entry and time.time() - entry.get("_ts", 0) < CACHE_TTL:
        return entry
    data = gh_api(f"https://api.github.com/repos/{full_name}")
    if "error" in data:
        meta = {"_ts": time.time(), "error": data["error"]}
    else:
        meta = {
            "_ts": time.time(),
            "full_name": data.get("full_name", full_name),
            "stars": data.get("stargazers_count", 0),
            "forks": data.get("forks_count", 0),
            "fork": bool(data.get("fork")),
            "archived": bool(data.get("archived")),
            "parent": (data.get("parent") or {}).get("full_name"),
            "pushed_at": data.get("pushed_at"),
            "description": (data.get("description") or "")[:160],
        }
    cache[full_name] = meta
    return meta


# --------------------------------------------------------------------------- #
# search + canonicalisation
# --------------------------------------------------------------------------- #

FIND_LINE = re.compile(r"^(\S+@\S+)\s+([\d.]+)\s*([KMB]?)\s+installs?\b")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def parse_installs(num: str, suffix: str) -> int:
    mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[suffix]
    try:
        return int(float(num) * mult)
    except ValueError:
        return 0


def run_find(query: str, owner: str | None) -> list[dict]:
    cmd = ["npx", "--yes", "skills@latest", "find", query]
    if owner:
        cmd += ["--owner", owner]
    env = {**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0"}
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180, check=False, env=env
        ).stdout
        out = ANSI.sub("", out)
    except Exception as e:  # noqa: BLE001
        print(f"error: could not run the skills CLI: {e}", file=sys.stderr)
        return []

    results = []
    for line in out.splitlines():
        m = FIND_LINE.match(line.strip())
        if not m:
            continue
        spec, num, suf = m.group(1), m.group(2), m.group(3)
        repo, _, skill = spec.partition("@")
        results.append(
            {
                "skill": skill,
                "repo": repo,
                "installs": parse_installs(num, suf),
                "installs_raw": f"{num}{suf}",
                "url": f"https://skills.sh/{repo}/{skill}",
            }
        )
    return results


def canonicalise(results: list[dict], cache: dict) -> list[dict]:
    """Group by skill name, resolve the canonical repo, count suppressed clones."""
    by_skill: dict[str, list[dict]] = {}
    for r in results:
        by_skill.setdefault(r["skill"], []).append(r)

    groups = []
    for skill, rows in by_skill.items():
        seen_repos: set[str] = set()
        candidates = []
        for r in rows:
            if r["repo"] in seen_repos:
                continue
            seen_repos.add(r["repo"])
            meta = repo_meta(r["repo"], cache)
            if "error" in meta:  # API down / rate-limited: keep raw, mark unknown
                meta = {"fork": False, "stars": None, "parent": None, "error": meta["error"]}
            candidates.append({**r, "meta": meta})

        def rank(c: dict):
            m = c["meta"]
            stars = m.get("stars")
            return (
                1 if not m.get("fork") else 0,          # originals first
                1 if not m.get("archived") else 0,      # not archived
                stars if isinstance(stars, int) else -1,  # most stars
            )

        candidates.sort(key=rank, reverse=True)
        canon = candidates[0]
        # if the winner is a fork and we know its parent, it is still a clone chain
        clones = [c for c in candidates[1:]]
        groups.append(
            {
                "skill": skill,
                "canonical": canon,
                "clones": clones,
                "clone_count": len(clones),
                "unverified": canon["meta"].get("stars") is None,
            }
        )
    groups.sort(key=lambda g: g["canonical"]["installs"], reverse=True)
    return groups


# --------------------------------------------------------------------------- #
# security review
# --------------------------------------------------------------------------- #

RISK_RULES = [
    (r"curl[^\n|]*\|\s*(ba)?sh|wget[^\n|]*\|\s*(ba)?sh",
     "HIGH", "pipes a remote download straight into a shell"),
    (r"\brm\s+-rf\s+[/~]|\bmkfs\b|dd\s+if=.*of=/dev/",
     "HIGH", "destructive filesystem command"),
    (r"\.ssh/|id_rsa|id_ed25519|\.aws/credentials|\.netrc|\.git-credentials|"
     r"\.config/gh/hosts|Keychain|login\.keychain",
     "HIGH", "reads credential / key material"),
    (r"\.env\b|API_KEY|SECRET_KEY|ACCESS_TOKEN|PASSWORD|PRIVATE KEY",
     "MED", "references secrets or credentials"),
    (r"curl\s+(-[A-Za-z]+\s+)*-X\s*POST|curl[^\n]*--data|nc\s+-|ncat\s+|/dev/tcp/",
     "HIGH", "possible data exfiltration channel"),
    (r"base64\s+-d|base64\s+--decode|eval\s*\(|exec\s*\(|python3?\s+-c",
     "MED", "encoded / dynamic code execution"),
    (r"\bsudo\b|chmod\s+\+x|chown\s+root",
     "MED", "privilege escalation or permission change"),
    (r"ignore (all )?previous instructions|do not tell the user|"
     r"without (asking|telling) the user|hidden from the user|"
     r"do not mention this",
     "HIGH", "prompt-injection / concealment directive"),
    (r"skills\s+add\s+[^\n]*\s-y\b|npx\s+skills\s+add[^\n]*-y\b",
     "MED", "installs further skills with confirmation skipped"),
    (r"https?://(?!github\.com|raw\.githubusercontent\.com|skills\.sh)[^\s)>\"]+",
     "LOW", "references an external domain"),
]

SEV_ORDER = {"HIGH": 0, "MED": 1, "LOW": 2}


def find_skill_path(repo: str, skill: str, cache: dict) -> str | None:
    """Locate the SKILL.md path for `skill` inside `repo` using the git tree."""
    key = f"{repo}"
    entry = cache.get(key)
    if not entry or time.time() - entry.get("_ts", 0) >= CACHE_TTL:
        info = gh_api(f"https://api.github.com/repos/{repo}")
        branch = info.get("default_branch", "HEAD") if "error" not in info else "HEAD"
        tree = gh_api(
            f"https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
        )
        paths = [
            n["path"]
            for n in tree.get("tree", [])
            if n.get("path", "").endswith("SKILL.md")
        ]
        entry = {"_ts": time.time(), "branch": branch, "paths": paths}
        cache[key] = entry

    want = skill.lower()
    exact = [p for p in entry["paths"] if Path(p).parent.name.lower() == want]
    if exact:
        return exact[0]
    partial = [p for p in entry["paths"] if want in p.lower()]
    if partial:
        return partial[0]
    if len(entry["paths"]) == 1:
        return entry["paths"][0]
    return None


def inspect_spec(spec: str, blob_cache: dict) -> dict:
    repo, _, skill = spec.partition("@")
    if not skill:
        skill = Path(repo).name
    path = find_skill_path(repo, skill, blob_cache.setdefault("_tree", {}))
    if not path:
        return {"spec": spec, "error": "could not locate SKILL.md in the repo"}

    url = f"https://raw.githubusercontent.com/{repo}/HEAD/{path}"
    text = raw_get(url)
    if text is None:
        return {"spec": spec, "error": f"could not fetch {url}"}

    findings = []
    lines = text.splitlines()
    for i, line in enumerate(lines, 1):
        for pattern, sev, why in RISK_RULES:
            if re.search(pattern, line, re.IGNORECASE):
                findings.append(
                    {"line": i, "sev": sev, "why": why, "text": line.strip()[:120]}
                )
    findings.sort(key=lambda f: SEV_ORDER[f["sev"]])

    # de-duplicate identical (sev, why) hits, keep first line
    seen, uniq = set(), []
    for f in findings:
        k = (f["sev"], f["why"])
        if k not in seen:
            seen.add(k)
            uniq.append(f)

    verdict = "CLEAN"
    if any(f["sev"] == "HIGH" for f in uniq):
        verdict = "REVIEW REQUIRED"
    elif any(f["sev"] == "MED" for f in uniq):
        verdict = "CAUTION"

    return {
        "spec": spec,
        "repo": repo,
        "skill": skill,
        "path": path,
        "url": url,
        "bytes": len(text),
        "lines": len(lines),
        "verdict": verdict,
        "findings": uniq,
    }


# --------------------------------------------------------------------------- #
# curated domain bundles
# --------------------------------------------------------------------------- #

DOMAINS = {
    "video": ["video editing", "ai video", "image to video", "video generation", "remotion"],
    "media": ["image generation", "ai music", "audio", "tts", "subtitle"],
    "automation": ["n8n", "workflow automation", "zapier", "make automation", "webhook"],
    "social": ["twitter automation", "linkedin", "instagram", "tiktok", "reddit automation"],
    "marketing": ["seo audit", "copywriting", "landing page", "email marketing", "analytics"],
}


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #

def fmt_stars(meta: dict) -> str:
    s = meta.get("stars")
    if not isinstance(s, int):
        return "?"
    if s >= 1000:
        return f"{s/1000:.1f}k"
    return str(s)


def fmt_installs(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def render_groups(groups: list[dict], limit: int | None) -> str:
    lines = []
    shown = groups[:limit] if limit else groups
    for g in shown:
        c = g["canonical"]
        m = c["meta"]
        flags = []
        if m.get("fork"):
            flags.append("FORK")
        if m.get("archived"):
            flags.append("ARCHIVED")
        if g["unverified"]:
            flags.append("UNVERIFIED")
        if isinstance(m.get("stars"), int) and m["stars"] < 100:
            flags.append("LOW-STARS")
        flag_s = ("  [" + ", ".join(flags) + "]") if flags else ""

        lines.append(f"◆ {g['skill']}{flag_s}")
        lines.append(
            f"    repo      {c['repo']}   ★{fmt_stars(m)}"
            f"   {fmt_installs(c['installs'])} installs"
        )
        if m.get("description"):
            lines.append(f"    about     {m['description']}")
        lines.append(f"    install   npx skills add {c['repo']}@{g['skill']}")
        lines.append(f"    more      {c['url']}")
        if g["clone_count"]:
            names = ", ".join(sorted({cl["repo"] for cl in g["clones"]}))
            lines.append(f"    clones    {g['clone_count']} suppressed: {names}")
        lines.append("")
    if not shown:
        lines.append("No skills matched.")
    lines.append(
        "Verify before installing: skill-scout inspect <owner/repo@skill>"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def cmd_search(args) -> int:
    query = " ".join(args.query)
    results = run_find(query, args.owner)
    if not results:
        print(f"No results for {query!r}.")
        return 1
    repo_cache = _load(REPO_CACHE)
    groups = canonicalise(results, repo_cache)
    _save(REPO_CACHE, repo_cache)

    if args.json:
        print(json.dumps(groups, indent=2))
    else:
        kept = sum(1 for g in groups)
        clones = sum(g["clone_count"] for g in groups)
        print(f"\nquery: {query!r}   {len(results)} raw hits"
              f" → {kept} canonical skills   ({clones} clones suppressed)\n")
        print(render_groups(groups, args.limit))
    return 0


def cmd_inspect(args) -> int:
    blob_cache = _load(BLOB_CACHE)
    report = inspect_spec(args.spec, blob_cache)
    _save(BLOB_CACHE, blob_cache)

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    if report.get("error"):
        print(f"inspect: {report['error']}")
        return 1

    print(f"\n{report['spec']}")
    print(f"  file     {report['url']}")
    print(f"  size     {report['lines']} lines / {report['bytes']} bytes")
    print(f"  verdict  {report['verdict']}\n")
    if not report["findings"]:
        print("  No risky patterns matched. Still read it yourself before installing.")
    else:
        for f in report["findings"]:
            print(f"  [{f['sev']:4}] L{f['line']:<4} {f['why']}")
            print(f"         {f['text']}")
    print()
    return 0


def cmd_domains(args) -> int:
    for name, queries in DOMAINS.items():
        print(f"{name:12} {' | '.join(queries)}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="skill-scout", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="search the registry, clones collapsed")
    s.add_argument("query", nargs="+")
    s.add_argument("--owner", help="restrict to one GitHub owner")
    s.add_argument("--json", action="store_true")
    s.add_argument("--limit", type=int, default=None)
    s.set_defaults(func=cmd_search)

    i = sub.add_parser("inspect", help="security-review a skill's SKILL.md")
    i.add_argument("spec", help="owner/repo@skill")
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=cmd_inspect)

    d = sub.add_parser("domains", help="list curated domain bundles")
    d.set_defaults(func=cmd_domains)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
