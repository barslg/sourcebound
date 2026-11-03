# Security policy

## Reporting a vulnerability

Do not open a public issue for a security problem. Report it privately through
GitHub: <https://github.com/barslg/sourcebound/security/advisories/new>
("Report a vulnerability"). You will get an answer within seven days.

## What counts

- Any way to read another author's Meta API key, a learner's answers, or a
  course that is not published.
- Any way to make the service call the Meta API on a key that is not the
  caller's own.
- A prompt-injection path by which source text can change what a learner is
  shown as verified.

## Supported versions

Only the `main` branch is supported; there are no released versions yet.

## What is already in place

- No secret value lives in the repository: `.env` is ignored, `.env.example`
  holds names only, and `gitleaks` runs in CI and pre-commit over the whole
  history (`.gitleaks.toml`).
- A public-tree gate (`scripts/check_public_tree.py`) fails CI on key shapes,
  dumps, private paths and non-example e-mail addresses.
- Per-user Meta API keys are stored encrypted (`user_model_keys`) and never
  returned by any route; the sentinel world in `tests/test_sentinels.py`
  proves no key value leaves a learner route.
- Dependabot watches Python, npm and GitHub Actions dependencies weekly.
