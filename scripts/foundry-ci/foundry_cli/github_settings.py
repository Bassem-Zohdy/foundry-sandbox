"""GitHub protection for a product repo: the branch ruleset and the production environment.

`plan_settings` reads the current GitHub state and diffs it against the payloads
built here. `apply_settings` sends only the changes. Nothing is written without
`--apply` on the CLI.
"""
import argparse
import copy
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .cli import register
from .config import Config, ConfigError, load_config
from .gh import GhError, GitHub, get_github

RULESET_NAME = "foundry-base"
REQUIRED_CHECKS = ("foundry / attribution", "foundry / plan-approved",
                   "foundry / gate-approved")
# All three are job names in template/.github/workflows/foundry-governance.yml.
# Each job always runs and passes "not applicable" when it does not apply.
# Pinning the app id stops another app from satisfying a check with the same name.
GITHUB_ACTIONS_APP_ID = 15368

# Plan limits (checked 2026-11-05 at
# https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/managing-environments-for-deployment):
# GitHub Free can configure environments only for public repositories; private
# repositories need GitHub Pro (user) or Team/Enterprise (org), and the page warns
# that some environment features, required reviewers among them, have limited
# availability on private repositories, so confirm for the account. Rulesets on
# private repositories likewise need a paid plan. When GitHub refuses a call for
# plan reasons we report `blocked: <reason>` instead of failing.
_PLAN_WORDS = ("upgrade", "plan")


@dataclass
class SettingChange:
    target: str  # "ruleset" | "environment"
    action: str  # "create" | "update" | "unchanged" | "blocked"
    method: str = ""
    path: str = ""
    body: Optional[dict] = None
    reason: str = ""
    diff: List[str] = field(default_factory=list)  # "+ added" / "~ raised" lines, never removals
    warnings: List[str] = field(default_factory=list)
    followups: List[Tuple[str, str, dict]] = field(default_factory=list)  # calls sent after the main one


def build_ruleset(config: Config) -> dict:
    team = config.mode == "team"
    return {
        "name": RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        # No bypass on purpose: in solo mode an admin passes the checks too.
        "bypass_actors": [],
        "conditions": {"ref_name": {"include": [f"refs/heads/{config.base_branch}"],
                                    "exclude": []}},
        "rules": [
            {"type": "pull_request", "parameters": {
                "required_approving_review_count": 1 if team else 0,
                "require_code_owner_review": team,
                "dismiss_stale_reviews_on_push": True,
                "require_last_push_approval": team,
                "required_review_thread_resolution": True,
            }},
            {"type": "required_status_checks", "parameters": {
                # Not strict: that would force a rebase before every merge.
                "strict_required_status_checks_policy": False,
                "required_status_checks": [
                    {"context": c, "integration_id": GITHUB_ACTIONS_APP_ID}
                    for c in REQUIRED_CHECKS],
            }},
            {"type": "non_fast_forward"},
            {"type": "deletion"},
        ],
    }


def build_environment(config: Config, tech_lead_id: int) -> dict:
    return {
        "reviewers": [{"type": "User", "id": tech_lead_id}],
        "prevent_self_review": config.mode == "team",
        # Only the base branch may deploy. "protected branches" may not count
        # rulesets, so use a custom branch policy (the POST for it is a followup).
        "deployment_branch_policy": {"protected_branches": False,
                                     "custom_branch_policies": True},
    }


_PR_KEYS = ("required_approving_review_count", "require_code_owner_review",
            "dismiss_stale_reviews_on_push", "require_last_push_approval",
            "required_review_thread_resolution")
_PLAN_RE = re.compile(
    r"upgrade|your plan|billing plan|current plan|plan limit|plan requirement", re.I)


def is_plan_limit(err: GhError) -> bool:
    """403/422 whose response text (not the request path) names a plan requirement."""
    return err.status in (403, 422) and bool(_PLAN_RE.search(err.message or ""))


def _is_not_found(err: GhError) -> bool:
    return err.status == 404


def _merge_ruleset(current: dict, config: Config) -> Tuple[dict, List[str], List[str]]:
    """Raise `current` to Foundry's floor. Returns (body, diff lines, warnings).

    Only adds or raises: extra rules, extra checks, merge methods, conditions,
    enforcement and bypass actors already there are kept.
    """
    desired = build_ruleset(config)
    body = {k: copy.deepcopy(current[k])
            for k in ("name", "target", "enforcement", "bypass_actors", "conditions")
            if k in current}
    for k in ("name", "target", "enforcement", "bypass_actors", "conditions"):
        body.setdefault(k, copy.deepcopy(desired[k]))
    rules = [{k: copy.deepcopy(v) for k, v in r.items() if k in ("type", "parameters")}
             for r in current.get("rules") or []]
    by_type = {r["type"]: r for r in rules}
    diff: List[str] = []
    for want in desired["rules"]:
        kind = want["type"]
        have = by_type.get(kind)
        if have is None:
            rules.append(copy.deepcopy(want))
            diff.append(f"+ rule {kind}")
        elif kind == "pull_request":
            params = have.setdefault("parameters", {})
            for key in _PR_KEYS:
                need, got = want["parameters"][key], params.get(key)
                if got is None:
                    params[key] = need
                    diff.append(f"+ pull_request.{key}: {need}")
                elif isinstance(need, bool):
                    if need and not got:
                        params[key] = True
                        diff.append(f"~ pull_request.{key}: {got} -> True")
                elif got < need:
                    params[key] = need
                    diff.append(f"~ pull_request.{key}: {got} -> {need}")
        elif kind == "required_status_checks":
            params = have.setdefault("parameters", {})
            params.setdefault("strict_required_status_checks_policy", False)
            checks = params.setdefault("required_status_checks", [])
            for c in want["parameters"]["required_status_checks"]:
                found = next((x for x in checks if x.get("context") == c["context"]), None)
                if found is None:
                    checks.append(dict(c))
                    diff.append(f"+ required check {c['context']} (integration {c['integration_id']})")
                elif found.get("integration_id") != c["integration_id"]:
                    diff.append(f"~ required check {c['context']} integration_id: "
                                f"{found.get('integration_id')} -> {c['integration_id']}")
                    found["integration_id"] = c["integration_id"]
    body["rules"] = rules
    warnings = []
    if body["bypass_actors"]:
        warnings.append("bypass_actors is not empty; admins or apps can skip the checks "
                        "(left unchanged)")
    if body["enforcement"] != "active":
        warnings.append(f"enforcement is {body['enforcement']!r}, not 'active' (left unchanged)")
    return body, diff, warnings


def _reviewers_of(env: dict) -> Tuple[List[Tuple[str, Any]], bool, int]:
    reviewers, prevent, wait = [], False, 0
    for rule_ in env.get("protection_rules") or []:
        if rule_.get("type") == "required_reviewers":
            prevent = bool(rule_.get("prevent_self_review"))
            for r in rule_.get("reviewers") or []:
                reviewers.append((r.get("type") or "User", (r.get("reviewer") or {}).get("id")))
        elif rule_.get("type") == "wait_timer":
            wait = int(rule_.get("wait_timer") or 0)
    return reviewers, prevent, wait


def _merge_environment(current: dict, config: Config,
                       lead_id: int) -> Tuple[dict, List[str]]:
    """Make sure the lead is a reviewer and self-review is not weaker than the mode requires."""
    reviewers, prevent, wait = _reviewers_of(current)
    diff: List[str] = []
    if ("User", lead_id) not in reviewers:
        reviewers.append(("User", lead_id))
        diff.append(f"+ reviewer {config.tech_lead}")
    want_prevent = config.mode == "team"
    if want_prevent and not prevent:
        diff.append("~ prevent_self_review: False -> True")
        prevent = True
    body: Dict[str, Any] = {
        "reviewers": [{"type": t, "id": i} for t, i in reviewers],
        "prevent_self_review": prevent,
        "deployment_branch_policy": current.get("deployment_branch_policy"),
    }
    if wait:
        body["wait_timer"] = wait
    if "can_admins_bypass" in current:  # PUT resets it to true if omitted
        body["can_admins_bypass"] = current["can_admins_bypass"]
    return body, diff


def _list_rulesets(gh: GitHub, base: str) -> List[dict]:
    out: List[dict] = []
    page = 1
    while True:
        batch = gh.api("GET", f"{base}?per_page=100&page={page}")
        if not isinstance(batch, list):
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out


def _plan_ruleset(gh: GitHub, config: Config) -> SettingChange:
    base = f"repos/{config.repo}/rulesets"
    existing = next((r for r in _list_rulesets(gh, base)
                     if r.get("name") == RULESET_NAME
                     and r.get("source_type", "Repository") == "Repository"), None)
    if existing is None:
        return SettingChange("ruleset", "create", "POST", base, build_ruleset(config))
    path = f"{base}/{existing['id']}"
    body, diff, warnings = _merge_ruleset(gh.api("GET", path), config)
    return SettingChange("ruleset", "update" if diff else "unchanged", "PUT", path, body,
                         diff=diff, warnings=warnings)


def _plan_environment(gh: GitHub, config: Config) -> SettingChange:
    path = f"repos/{config.repo}/environments/production"
    lead_id = gh.api("GET", f"users/{config.tech_lead}")["id"]
    try:
        current = gh.api("GET", path)
    except GhError as e:
        if not _is_not_found(e):
            raise
        return SettingChange(
            "environment", "create", "PUT", path, build_environment(config, lead_id),
            followups=[("POST", f"{path}/deployment-branch-policies",
                        {"name": config.base_branch, "type": "branch"})])
    body, diff = _merge_environment(current, config, lead_id)
    return SettingChange("environment", "update" if diff else "unchanged", "PUT", path, body,
                         diff=diff)


def plan_settings(gh: GitHub, config: Config) -> List[SettingChange]:
    changes = []
    for target, fn in (("ruleset", _plan_ruleset), ("environment", _plan_environment)):
        try:
            changes.append(fn(gh, config))
        except GhError as e:
            if not is_plan_limit(e):
                raise
            changes.append(SettingChange(target, "blocked", reason=e.message))
    return changes


def apply_settings(gh: GitHub, changes: List[SettingChange]) -> List[SettingChange]:
    """Send create/update changes. Returns the blocked ones (planned or hit now)
    and `partial` ones (main call done, a followup failed)."""
    blocked: List[SettingChange] = []
    for c in changes:
        if c.action == "blocked":
            blocked.append(c)
        elif c.action in ("create", "update"):
            try:
                gh.api(c.method, c.path, c.body)
            except GhError as e:
                if not is_plan_limit(e):
                    raise
                blocked.append(SettingChange(c.target, "blocked", c.method, c.path, c.body,
                                             reason=e.message))
                continue
            for method, path, body in c.followups:
                try:
                    gh.api(method, path, body)
                except GhError as e:
                    # The main call already succeeded: report what is half done.
                    blocked.append(SettingChange(
                        c.target, "partial", method, path, body,
                        reason=f"{c.target} created, branch policy not set \u2014 {e.message}"))
                    break
    return blocked


def _describe(c: SettingChange) -> str:
    name = RULESET_NAME if c.target == "ruleset" else "production"
    if c.action == "blocked":
        return f"{c.target} {name}: blocked: {c.reason}"
    suffix = f" ({c.method} {c.path})" if c.action in ("create", "update") else ""
    lines = [f"{c.target} {name}: {c.action}{suffix}"]
    lines += [f"    {d}" for d in c.diff]
    lines += [f"    warn: {w}" for w in c.warnings]
    lines += [f"    then {m} {p}" for m, p, _ in c.followups]
    return "\n".join(lines)


def _configure(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=".", help="repo root containing foundry.json")


def _configure_apply(p: argparse.ArgumentParser) -> None:
    _configure(p)
    p.add_argument("--apply", action="store_true", help="write to GitHub (default is a dry run)")


def _load(ns: argparse.Namespace) -> Optional[Config]:
    try:
        return load_config(Path(ns.repo))
    except ConfigError as e:
        for problem in e.problems:
            print(problem, file=sys.stderr)
        return None


def _run_plan(ns: argparse.Namespace) -> int:
    config = _load(ns)
    if config is None:
        return 2
    changes = plan_settings(get_github(), config)
    for c in changes:
        print(_describe(c))
    return 0


def _run_apply(ns: argparse.Namespace) -> int:
    config = _load(ns)
    if config is None:
        return 2
    gh = get_github()
    changes = plan_settings(gh, config)
    for c in changes:
        print(_describe(c))
    if not ns.apply:
        print("dry run: nothing written. Re-run with --apply to apply these changes.")
        return 0
    blocked = apply_settings(gh, changes)
    for c in blocked:
        label = "partial" if c.action == "partial" else f"blocked: {c.target}"
        print(f"{label}: {c.reason}", file=sys.stderr)
    if blocked:
        print(f"foundry: {len(blocked)} change(s) blocked or partial; "
              "the others were applied", file=sys.stderr)
        return 1
    print("applied")
    return 0


def register_commands() -> None:
    register("github plan", _run_plan, _configure)
    register("github apply", _run_apply, _configure_apply)
