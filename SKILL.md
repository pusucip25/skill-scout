---
name: skill-scout
description: Use when searching for, evaluating, or installing agent skills from the open registry (skills.sh / `npx skills`) — including "find a skill for X", "is there a skill that does X", or any request to extend agent capabilities. Returns CANONICAL skills with clone/mirror duplicates collapsed, and security-reviews the SKILL.md before anything is installed.
---

# Skill Scout

Clean, canonical, security-checked discovery for the open agent-skills registry.

## The problem it solves

The public registry (`skills.sh`, queried through `npx skills find`) has two defects
that make naive use unsafe:

1. **Clone pollution.** The same skill is republished under many owners, and every
   copy reports a large install count. A skill shown as *"710K installs"* can come
   from a 5-star mirror while the 296K-star original is buried below it. A high
   install count is therefore **not** evidence of quality — the registry's own
   ranking is not trustworthy on its own.
2. **No security review.** A skill is arbitrary instructions the agent will follow
   with full agent permissions. There is no sandbox. The registry does not read
   the `SKILL.md` for you.

`skill-scout` fixes both: it groups results by skill name, resolves the original
repository, suppresses the clones, and scans the `SKILL.md` for risky instructions
before you install.

## When to use

- The user asks "how do I do X", "find a skill for X", "is there a skill that can X".
- The user wants to extend the agent with a domain (video, media, automation, social, marketing).
- You are about to recommend or install *any* skill from the open registry.
- You need to vet a third-party skill before it runs.

## Commands

```bash
S=scripts/skill_scout.py        # adjust path to where this skill lives

# 1. Search — clones collapsed, canonical repo resolved
python3 $S search "video editing"
python3 $S search "n8n automation"
python3 $S search "react performance" --owner vercel     # scope to one owner
python3 $S search "seo audit" --json                     # machine-readable
python3 $S search "video editing" --limit 5              # top N only

# 2. Inspect — security-review a SKILL.md before installing
python3 $S inspect czlonkowski/n8n-skills@n8n-workflow-patterns
python3 $S inspect vercel-labs/skills@find-skills --json

# 3. Domains — curated query bundles (video, media, automation, social, marketing)
python3 $S domains
```

Set `GITHUB_TOKEN` (or `GH_TOKEN`) to raise the GitHub API limit from 60 to
5000 requests/hour. Without a token, frequent searches can return `[UNVERIFIED]`
rows — the skill still appears, but its star/fork status could not be confirmed.
Metadata is cached for 24h under `~/.cache/skill-scout`.

## Required workflow

Never recommend or install a skill without running all four steps.

1. **Search** with `skill-scout search "<domain or task>"`. Use specific keywords
   ("react testing" beats "testing"); try alternates if a term returns nothing.
2. **Read the result's flags.** Treat these as warnings, not decoration:
   - `[FORK]` / `[LOW-STARS]` — the canonical pick is itself a fork or has <100 stars;
     say so explicitly and consider a different skill.
   - `[ARCHIVED]` — the upstream repo is archived; prefer an active alternative.
   - `[UNVERIFIED]` — GitHub metadata could not be fetched. Do not present the
     install count as a quality signal.
   - `clones N suppressed` — report this. It means the raw ranking was inflated.
3. **Inspect before recommending**: `skill-scout inspect <owner/repo@skill>`.
   - `CLEAN` — no risky patterns; still skim the file.
   - `CAUTION` — MED findings (secrets, `sudo`, dynamic exec, auto-install).
     Tell the user what matched; let them decide.
   - `REVIEW REQUIRED` — HIGH findings (shell-from-network, credential access,
     exfiltration, prompt-injection). **Do not recommend installing.** Report the
     finding verbatim and stop.
4. **Install only with explicit confirmation, scoped and copied:**

   ```bash
   npx skills add <owner/repo@skill> -g -a <agent> --copy
   ```

   Use `-g` (user-level), never `-y` by default, and target one agent rather than
   `--agent '*'`. Add `-y` only when the user asked for a non-interactive install
   *in this turn*, after steps 1–3 passed.

## Non-negotiable safety rules

- **Never install unprompted.** "Find me a skill" is a search request, not consent to install.
- **Never pass `-y` on a skill you have not inspected.**
- **Never recommend a skill you have not read.** `inspect` reads it; read the report.
- **Prefer official sources** (`vercel-labs`, `anthropics`, `microsoft`, `openai`) and
  report the source repo to the user for anything else.
- **Report the clone count.** If the ranking was inflated, the user should know.

## Limitations

- Canonicalisation is a heuristic: it prefers a non-fork repo with the most stars,
  which can misidentify the original when a mirror has been starred more heavily.
- `inspect` is a pattern scanner, not a proof of safety. A clean verdict means
  "no known-bad patterns", not "trusted".
- Star/fork data comes from the unauthenticated GitHub API unless a token is set;
  it can rate-limit and degrade rows to `[UNVERIFIED]`.
- The curated domain bundles are starting points, not exhaustive coverage.
