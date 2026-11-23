"""Safe calculator evaluator (no sandbox)."""

from __future__ import annotations

import math
import pytest

from agent.calc_eval import CalcEvalError, evaluate_expression, normalize_spoken_math, try_tier0_calc, try_evaluate_spoken


def test_basic_arithmetic():
    assert evaluate_expression("17 * 23") == 391
    assert evaluate_expression("2 ** 10") == 1024


def test_math_functions():
    assert abs(evaluate_expression("sqrt(2)") - math.sqrt(2)) < 1e-9
    assert evaluate_expression("round(3.7)") == 4


def test_rejects_attribute_access():
    with pytest.raises(CalcEvalError):
        evaluate_expression("__import__('os')")


def test_rejects_huge_exponent():
    with pytest.raises(CalcEvalError):
        evaluate_expression("2 ** 200")


def test_spoken_normalization():
    assert "17 * 23" in normalize_spoken_math("what is 17 times 23")
    spoken = try_evaluate_spoken("what is 17 times 23")
    assert spoken is not None
    assert spoken.value == 391


def test_percent_of():
    spoken = try_evaluate_spoken("15% of 240")
    assert spoken is not None
    assert spoken.value == pytest.approx(36.0)


def test_gst():
    spoken = try_evaluate_spoken("240 plus GST")
    assert spoken is not None
    assert spoken.value == pytest.approx(264.0)


def test_tier0_payload():
    out = try_tier0_calc("what is 17 times 23")
    assert out is not None
    assert "391" in out["reply"]