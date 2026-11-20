# venator.rules — one module per deterministic guard rule.
# Each module exposes an evaluate(ctx, analysis, recipient, ...) callable whose
# name matches an entry in policy.yaml `rules:`. First match wins; rule name is
# logged. A rule may only ever TIGHTEN a decision (escalate or hold) — no rule
# in this package is permitted to auto-clear on message text.
