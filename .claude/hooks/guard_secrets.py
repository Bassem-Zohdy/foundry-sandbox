#!/usr/bin/env python3
"""PreToolUse (Write|Edit|MultiEdit): block writing secrets or secrets files.

Blocks writing `.env*` (except `.env.example`) and content that matches a
live-token pattern. Checks `content` (Write), `new_string` (Edit) and
`edits[].new_string` (MultiEdit). Exit 2 blocks, with the reason on stderr.
"""
import json
import re
import sys

_L = r"(?<![A-Za-z0-9])"  # left boundary: do not match inside a longer word
TOKEN_PATTERNS = [
    (_L + r"sk-[A-Za-z0-9]{20,}", "API secret key (sk-)"),
    (_L + r"sk-ant-[A-Za-z0-9_-]{20,}", "Anthropic API key"),
    (_L + r"sk-proj-[A-Za-z0-9_-]{20,}", "OpenAI project key"),
    (_L + r"sk_live_[A-Za-z0-9]{20,}", "Stripe live key"),
    (_L + r"ghp_[A-Za-z0-9]{36}", "GitHub personal access token"),
    (_L + r"gh[oprsu]_[A-Za-z0-9]{36}", "GitHub token"),
    (_L + r"github_pat_[A-Za-z0-9_]{22,}", "GitHub fine-grained token"),
    (_L + r"xox[bp]-", "Slack token"),
    (_L + r"AKIA[0-9A-Z]{16}", "AWS access key id"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
]


def block(reason):
    print(reason, file=sys.stderr)
    sys.exit(2)


def written_text(tool_input):
    parts = [tool_input.get("content"), tool_input.get("new_string")]
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        parts.extend(e.get("new_string") for e in edits if isinstance(e, dict))
    return "\n".join(p for p in parts if isinstance(p, str))


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        sys.exit(0)

    path = str(tool_input.get("file_path") or "")
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        block("BLOCKED: refusing to write %s. Secrets are never written by the agent; edit it by hand "
              "and keep .env.example in sync with placeholder values." % name)

    text = written_text(tool_input)
    for pattern, label in TOKEN_PATTERNS:
        if re.search(pattern, text):
            block("BLOCKED: the content contains what looks like a live %s. A committed credential "
                  "must be rotated, so this is blocked before the write. Use a placeholder." % label)
    sys.exit(0)


if __name__ == "__main__":
    main()
