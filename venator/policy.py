# policy.py - loads and validates config/policy.yaml.
#
# Nothing in this system owns a threshold. guard.py, the curveball rules and the
# admin API all read their numbers from here. A missing key is a startup error,
# not a silent default, because a silently-defaulted safety limit is a bug that
# only shows up in front of people.

import os

try:
    import yaml
except ImportError:  # pragma: no cover - fallback so the demo runs on a bare venv
    yaml = None

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_PATH = os.path.join(_ROOT, "config", "policy.yaml")


class PolicyError(Exception):
    pass


def _mini_yaml(text):
    """Tiny YAML subset parser used only if PyYAML is not installed.

    Handles the shapes this file actually uses: nested mappings, scalar lists,
    booleans, numbers and comments. Enough so the demo never dies at startup.
    """
    def scalar(v):
        v = v.strip().strip('"').strip("'")
        if v in ("true", "True"):
            return True
        if v in ("false", "False"):
            return False
        try:
            return float(v) if "." in v else int(v)
        except ValueError:
            return v

    root = {}
    stack = [(-1, root)]  # type: ignore[var-annotated]
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        body = line.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if body.startswith("- "):
            item = body[2:].strip()
            if not isinstance(parent, list):
                continue
            parent.append(scalar(item))
            continue
        if ":" not in body:
            continue
        key, _, val = body.partition(":")
        key, val = key.strip(), val.strip()
        if val == "":
            container = [] if _next_is_list(text, raw) else {}
            parent[key] = container
            stack.append((indent, container))
        else:
            parent[key] = scalar(val)
    return root


def _next_is_list(text, current):
    seen = False
    for raw in text.splitlines():
        if raw is current:
            seen = True
            continue
        if seen:
            s = raw.split("#", 1)[0]
            if not s.strip():
                continue
            return s.strip().startswith("- ")
    return False


def load(path=None):
    """Load, normalise and validate the policy. Raises PolicyError on a bad file."""
    path = path or POLICY_PATH
    if not os.path.exists(path):
        raise PolicyError(f"policy file not found: {path}")
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    data = yaml.safe_load(text) if yaml else _mini_yaml(text)
    if not isinstance(data, dict):
        raise PolicyError("policy root must be a mapping")
    _validate(data)
    return data


_REQUIRED = (
    ("amounts", "auto_clear_limit"),
    ("amounts", "uncertain_escalate_above"),
    ("amounts", "human_confirm_always_above"),
    ("risk", "high"),
    ("risk", "min_confidence"),
    ("recipients", "scam_reports_escalate_at"),
)


def _validate(p):
    for section, key in _REQUIRED:
        if section not in p or key not in p[section]:
            raise PolicyError(f"policy is missing {section}.{key}")
    if p["risk"]["min_confidence"] > 1:
        raise PolicyError("risk.min_confidence must be a fraction between 0 and 1")
    if not isinstance(p.get("rules"), list) or not p["rules"]:
        raise PolicyError("policy.rules must list at least one rule name")
    if p["guardian"].get("minor_age_below", 0) <= 0:
        raise PolicyError("guardian.minor_age_below must be positive")
    if p["cancel_window"].get("seconds", 0) < 0:
        raise PolicyError("cancel_window.seconds cannot be negative")


class Policy:
    """Thin read-only view over the loaded mapping, with dotted access."""

    def __init__(self, data=None, path=None):
        self.data = data if data is not None else load(path)
        self.path = path or POLICY_PATH

    def get(self, dotted, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def __getitem__(self, dotted):
        sentinel = object()
        val = self.get(dotted, sentinel)
        if val is sentinel:
            raise PolicyError(f"policy has no key: {dotted}")
        return val

    @property
    def rules(self):
        return list(self.data.get("rules") or [])

    def rule_enabled(self, name):
        """A curveball rule is active if it is switched on in its own section
        AND listed in the rule order. Either switch turns it off cleanly."""
        if name not in self.rules:
            return False
        section = {
            "split_payment": "split_payment",
            "lookalike_vendor": "lookalike",
            "injection_attempt": "injection",
            "guardian_required": "guardian",
        }.get(name)
        if section is None:
            return True
        return bool(self.get(f"{section}.enabled", True))

    def describe(self):
        """Flat, human-readable dump used by the admin console and the API."""
        keys = [
            "amounts.auto_clear_limit", "amounts.uncertain_escalate_above",
            "amounts.human_confirm_always_above", "risk.high",
            "risk.mid_low", "risk.min_confidence",
            "split_payment.window_minutes", "split_payment.trigger_total",
            "split_payment.min_counted_amount",
            "lookalike.similarity_threshold",
            "cancel_window.seconds", "guardian.minor_age_below",
            "guardian.requires_approval_above",
            "recipients.scam_reports_escalate_at",
        ]
        out = []
        for k in keys:
            node = self.data
            for part in k.split("."):
                node = node.get(part, {}) if isinstance(node, dict) else {}
            if node != {}:
                out.append({"key": k, "value": node})
        return out


_CACHE = {}


def current(path=None):
    """Cached policy. Call reload() after an edit - the admin API does this."""
    key = path or POLICY_PATH
    if key not in _CACHE:
        _CACHE[key] = Policy(path=key)
    return _CACHE[key]


def reload(path=None):
    key = path or POLICY_PATH
    _CACHE.pop(key, None)
    return current(key)
