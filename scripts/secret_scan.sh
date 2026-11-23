#!/usr/bin/env bash
# Pre-push secret scan (working tree + optional git history via gitleaks).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV_PY="${ROOT}/venv/bin/python"
DETECT="${ROOT}/venv/bin/detect-secrets"
GITLEAKS="${GITLEAKS:-$HOME/.local/bin/gitleaks}"
# Baseline path must stay repo-relative (never an absolute path) for portable metadata.
BASELINE=".secrets.baseline"
FAIL=0

echo "==> Sahir secret scan (values are never printed)"

if [[ ! -x "$DETECT" ]]; then
  echo "Install detect-secrets: $VENV_PY -m pip install detect-secrets" >&2
  exit 2
fi

echo "==> detect-secrets (working tree vs baseline)"
if [[ ! -f "$BASELINE" ]]; then
  echo "WARN: missing $BASELINE — from repo root run: git ls-files | xargs venv/bin/detect-secrets scan > .secrets.baseline" >&2
  FAIL=1
else
  if ! "$DETECT" scan --baseline "$BASELINE" 2>/tmp/sahir-detect-secrets.err; then
    echo "FAIL: new potential secrets vs baseline" >&2
    sed 's/[A-Za-z0-9+/=]\{20,\}/[REDACTED]/g' /tmp/sahir-detect-secrets.err >&2 || true
    FAIL=1
  else
    echo "OK: working tree matches baseline"
  fi
fi

if [[ -x "$GITLEAKS" ]]; then
  echo "==> gitleaks (working tree)"
  if ! "$GITLEAKS" detect --source "$ROOT" --config "$ROOT/.gitleaks.toml" --redact -v 2>/tmp/sahir-gitleaks-tree.log; then
    echo "FAIL: gitleaks reported findings in working tree" >&2
    rg -N 'file:|RuleID|Fingerprint' /tmp/sahir-gitleaks-tree.log 2>/dev/null | sed 's/[A-Za-z0-9_\-]\{20,\}/[REDACTED]/g' >&2 || cat /tmp/sahir-gitleaks-tree.log >&2
    FAIL=1
  else
    echo "OK: gitleaks working tree clean"
  fi
  echo "==> gitleaks (full git history — slow)"
  if ! "$GITLEAKS" detect --source "$ROOT" --config "$ROOT/.gitleaks.toml" --redact --log-opts="--all" 2>/tmp/sahir-gitleaks-history.log; then
    echo "WARN: gitleaks reported historical findings — review with Michael before history rewrite" >&2
    rg -N 'file:|RuleID' /tmp/sahir-gitleaks-history.log 2>/dev/null | head -40 | sed 's/[A-Za-z0-9_\-]\{24,\}/[REDACTED]/g' >&2 || true
    FAIL=1
  else
    echo "OK: gitleaks history clean"
  fi
else
  echo "SKIP: gitleaks not found at $GITLEAKS (optional)"
fi

echo "==> Done (exit $FAIL)"
exit "$FAIL"