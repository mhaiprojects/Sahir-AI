"""Safe arithmetic evaluation + Tier-0 spoken-number calculator (no sandbox)."""

from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

_MAX_EXPONENT = 64
_MAX_MAGNITUDE = 1e15

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_ALLOWED_FUNCS: dict[str, Any] = {
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "round": round,
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "pow": pow,
}

_ALLOWED_NAMES = {"pi": math.pi, "e": math.e, "tau": math.tau}

_SPOKEN_REPLACEMENTS = (
    (re.compile(r"(?i)\bwhat(?:'s| is)\b"), ""),
    (re.compile(r"(?i)\bcalculate\b"), ""),
    (re.compile(r"(?i)\bcompute\b"), ""),
    (re.compile(r"(?i)\btimes\b"), "*"),
    (re.compile(r"(?i)\bmultiplied by\b"), "*"),
    (re.compile(r"(?i)\bdivided by\b"), "/"),
    (re.compile(r"(?i)\bover\b"), "/"),
    (re.compile(r"(?i)\bplus\b"), "+"),
    (re.compile(r"(?i)\bminus\b"), "-"),
    (re.compile(r"(?i)\bsquared\b"), "** 2"),
    (re.compile(r"(?i)\bcubed\b"), "** 3"),
    (re.compile(r"(?i)\bto the power of\b"), "**"),
    (re.compile(r"(?i)\bsquare root of\b"), "sqrt("),
    (re.compile(r"(?i)\broot of\b"), "sqrt("),
)

_PCT_OF = re.compile(r"(?is)(\d+(?:\.\d+)?)\s*%\s*of\s*(\d+(?:\.\d+)?)")
_PCT_TRAIL = re.compile(r"(?is)(\d+(?:\.\d+)?)\s*%")
_GST_RE = re.compile(r"(?is)(\d+(?:\.\d+)?)\s*(?:\+|plus)\s*gst\b")
_GST_ADD_RE = re.compile(r"(?is)(?:add\s+)?gst\s+(?:to\s+)?(\d+(?:\.\d+)?)")

_TIER0_QUESTION = re.compile(
    r"(?is)^(?:please\s+)?(?:what(?:'s| is)|calculate|compute)\s+.+$|"
    r"^(?:please\s+)?(?:square root|root)\s+of\s+.+$|"
    r"^\d+(?:\.\d+)?\s*%\s*of\s+\d"
)


class CalcEvalError(ValueError):
    pass


def normalize_spoken_math(text: str) -> str:
    t = (text or "").strip().rstrip("?.!…")
    for pat, repl in _SPOKEN_REPLACEMENTS:
        t = pat.sub(repl, t)
    t = re.sub(r"\s+", " ", t).strip()
    # balance sqrt( if spoken form left trailing
    if t.count("sqrt(") > t.count(")"):
        t += ")" * (t.count("sqrt(") - t.count(")"))
    return t


def _apply_percent_and_gst(t: str) -> tuple[str, list[str]]:
    notes: list[str] = []
    m = _GST_RE.search(t)
    if m:
        base = float(m.group(1))
        total = base * 1.1
        notes.append(f"{base:g} + 10% GST (Australia) → {total:g}")
        return str(total), notes
    m = _GST_ADD_RE.search(t)
    if m:
        base = float(m.group(1))
        total = base * 1.1
        notes.append(f"{base:g} + 10% GST (Australia) → {total:g}")
        return str(total), notes
    m = _PCT_OF.search(t)
    if m:
        pct, base = float(m.group(1)), float(m.group(2))
        val = base * pct / 100.0
        notes.append(f"{pct:g}% of {base:g}")
        return str(val), notes
    t2 = _PCT_TRAIL.sub(lambda x: f"({x.group(1)} / 100)", t)
    return t2, notes


def _check_magnitude(value: float | int) -> float:
    v = float(value)
    if not math.isfinite(v) or abs(v) > _MAX_MAGNITUDE:
        raise CalcEvalError("result out of allowed range")
    return v


def _safe_eval(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return _check_magnitude(node.value)
        raise CalcEvalError("only numeric constants allowed")
    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise CalcEvalError("operator not allowed")
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        if isinstance(node.op, ast.Pow):
            exp = right if isinstance(right, (int, float)) else None
            if exp is not None and abs(float(exp)) > _MAX_EXPONENT:
                raise CalcEvalError("exponent too large")
        out = op(left, right)
        return _check_magnitude(out)
    if isinstance(node, ast.UnaryOp):
        fn = _UNARY.get(type(node.op))
        if fn is None:
            raise CalcEvalError("unary operator not allowed")
        return _check_magnitude(fn(_safe_eval(node.operand)))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise CalcEvalError("only simple function calls allowed")
        fn = _ALLOWED_FUNCS.get(node.func.id)
        if fn is None:
            raise CalcEvalError(f"function {node.func.id!r} not allowed")
        if node.keywords:
            raise CalcEvalError("keyword arguments not allowed")
        args = [_safe_eval(a) for a in node.args]
        if node.func.id == "sum" and len(args) == 1 and isinstance(args[0], (list, tuple)):
            out = sum(args[0])
        else:
            out = fn(*args)
        return _check_magnitude(float(out))
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_NAMES:
            return _ALLOWED_NAMES[node.id]
        raise CalcEvalError(f"name {node.id!r} not allowed")
    raise CalcEvalError(f"expression type {type(node).__name__} not allowed")


def evaluate_expression(expr: str) -> float:
    expr = (expr or "").strip()
    if not expr:
        raise CalcEvalError("empty expression")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise CalcEvalError(f"invalid syntax: {exc.msg}") from exc
    return float(_safe_eval(tree))


def format_number(value: float, *, currency: bool = False) -> str:
    d = Decimal(str(value))
    if currency:
        return f"${d.quantize(Decimal('0.01')):,.2f}"
    if float(d) == int(d):
        return f"{int(d):,}"
    s = f"{float(d):,.6f}".rstrip("0").rstrip(".")
    return s


@dataclass
class CalcTier0Result:
    expression: str
    value: float
    notes: list[str]
    currency: bool


def try_evaluate_spoken(text: str) -> CalcTier0Result | None:
    raw = (text or "").strip()
    if not raw:
        return None
    currency = bool(re.search(r"(?i)\$|aud|dollar|gst|price|cost", raw))
    expr, notes = _apply_percent_and_gst(raw)
    if notes and not any(op in expr for op in "+-*/"):
        try:
            val = float(expr)
            return CalcTier0Result(expression=" ".join(notes), value=val, notes=notes, currency=currency)
        except ValueError:
            pass
    norm = normalize_spoken_math(expr)
    if not norm or not re.search(r"[\d)(+\-*/.^]|sqrt|pi\b", norm):
        return None
    try:
        val = evaluate_expression(norm)
    except CalcEvalError:
        return None
    return CalcTier0Result(expression=norm, value=val, notes=notes, currency=currency)


def try_tier0_calc(text: str) -> dict[str, Any] | None:
    """Deterministic calculator for simple utterances."""
    t = (text or "").strip()
    if not t:
        return None
    if not (
        _TIER0_QUESTION.search(t)
        or re.search(r"(?i)\b(\d+.*times|percent|square root|gst)\b", t)
    ):
        return None
    trial = try_evaluate_spoken(t)
    if trial is None:
        return None
    formatted = format_number(trial.value, currency=trial.currency)
    lines = [f"Expression: {trial.expression}", f"Result: {formatted}"]
    for n in trial.notes:
        if n not in trial.expression:
            lines.insert(1, n)
    reply = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, start=1))
    return {
        "reply": reply,
        "expression": trial.expression,
        "value": trial.value,
        "formatted": formatted,
        "numbered_lines": lines,
    }


def format_calc_worker_reply(
    *,
    lines: list[str],
    code: str | None = None,
    sandbox: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Build display text + assistant message meta."""
    display = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, start=1))
    meta: dict[str, Any] = {}
    if code:
        meta["calc_code"] = code
        meta["calc_sandbox"] = sandbox
    return display, meta