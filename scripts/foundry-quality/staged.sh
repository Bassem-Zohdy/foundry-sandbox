#!/bin/sh
# Pre-commit: hand each staged file to the hook of the pack whose root is nearest
# (scripts/foundry-quality/packs/<pack>/staged.sh <q> <root> <file>...), then compare the
# results for those files with the baseline. The repo-wide tools (architecture, dead code,
# duplication, Semgrep) run in run.sh and CI, not here, to keep commits fast.
#
# Exit: 0 ok or nothing to check, 1 new violations, 2 a hook crashed or git failed.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
. "$here/lib.sh"
cd "$repo"
q="$(quality_dir)/staged"
mkdir -p "$q"
rm -f "$q"/*.sarif

# Write to a file, not a pipe: a pipe would hide git's exit status. -z and quotepath=off keep
# non-ASCII (Arabic) and spaced paths whole.
git -c core.quotepath=off diff --cached -z --name-only --diff-filter=ACMR >"$q/staged.list" ||
  fail "git could not list the staged files"
[ -s "$q/staged.list" ] || exit 0
foundry pack roots >"$q/roots.tsv" || fail "the pack roots could not be read from foundry.json"

# Each file goes to the packs at its nearest root; each hook keeps the file types it checks.
# A pack without a staged.sh has no commit-time check.
python3 - "$q" "$repo/scripts/foundry-ci/run.py" <<'PY'
import os, subprocess, sys
q, run_py = sys.argv[1], sys.argv[2]
files = [f for f in open(os.path.join(q, "staged.list"), "rb").read().decode("utf-8", "surrogateescape").split("\0") if f]
roots = [line.rstrip("\n").split("\t", 1) for line in open(os.path.join(q, "roots.tsv"), encoding="utf-8") if "\t" in line]
def under(f, root):
    return root == "." or f.startswith(root + "/")
def depth(root):
    return 0 if root == "." else root.count("/") + 1
routed = {}
for f in files:
    mine = [(p, r) for p, r in roots if under(f, r)]
    if mine:
        best = max(depth(r) for _, r in mine)
        for p, r in mine:
            if depth(r) == best:
                routed.setdefault((p, r), []).append(f)
ran = set()
for pack, root in roots:
    hook = os.path.join("scripts", "foundry-quality", "packs", pack, "staged.sh")
    mine = routed.get((pack, root))
    if not mine or not os.path.isfile(hook):
        continue
    status = subprocess.run(["sh", hook, q, root, *mine], stdin=subprocess.DEVNULL).returncode
    if status != 0:
        print(f"foundry-quality: pack {pack}: {hook} exited {status}", file=sys.stderr)
        sys.exit(2)
    ran.update(mine)
sarif = sorted(n for n in os.listdir(q) if n.endswith(".sarif"))
if not sarif:
    sys.exit(0)
cli = [sys.executable, run_py] if os.path.isfile(run_py) else ["foundry"]
args = [a for n in sarif for a in ("--sarif", os.path.join(q, n))]
sys.exit(subprocess.run([*cli, "quality", "check", *args, "--paths", *[f for f in files if f in ran]]).returncode)
PY
