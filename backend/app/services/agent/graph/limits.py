"""Graph step ceiling derived from the caller's budget.

LangGraph's default recursion limit is 25 supersteps. This audit graph spends a
fixed prefix and suffix plus one superstep per file, so a repository only a
little larger than the default fails before the report node. The ceiling below
tracks the bounded plan (file cap and model-call cap). It is not a large
constant: a routing loop still stops once it has used the work the caller
actually authorized.
"""

from __future__ import annotations

from app.services.agent.domain import RunBudget

# 5 prefix nodes + 4 suffix nodes. A fresh Pregel run allows about
# `recursion_limit` node executions and fails on the next one, so the +1 lets
# the terminal node run. Extra slack covers the budget-stop iteration that
# records work which was not started.
_PIPELINE_NODES = 9
_TERMINAL_AND_SLACK = 8


def bounded_analysis_units(budget: RunBudget) -> int:
    """How many file steps this budget can authorize.

    ``max_files == 0`` means no file cap, so the model-call cap is the bound.
    When both caps are set, the smaller one wins.
    """
    file_units = budget.max_files if budget.max_files > 0 else None
    call_units = budget.max_model_calls if budget.max_model_calls > 0 else None
    if file_units is not None and call_units is not None:
        return max(1, min(file_units, call_units))
    if file_units is not None:
        return max(1, file_units)
    if call_units is not None:
        return max(1, call_units)
    return 1


def recursion_limit_for_budget(budget: RunBudget) -> int:
    """Step ceiling for one audit invoke, matched to ``budget``."""
    return _PIPELINE_NODES + bounded_analysis_units(budget) + _TERMINAL_AND_SLACK
