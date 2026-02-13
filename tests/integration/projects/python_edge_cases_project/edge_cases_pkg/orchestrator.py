"""Single entry point that exercises all Python parsing edge-cases."""

# Baseline (this file): references=3 classes=0 nodes=3 outgoing_edges=0 incoming_edges=0

from edge_cases_pkg.scenarios.a_basics import alpha, call_cross_module
from edge_cases_pkg.scenarios.b_nested import outer
from edge_cases_pkg.scenarios.c_methods import MethodPlayground, invoke_method_variants
from edge_cases_pkg.scenarios.d_inheritance import run_inheritance
from edge_cases_pkg.scenarios.e_decorators import run_decorator
from edge_cases_pkg.scenarios.f_async import run_async_sync
from edge_cases_pkg.scenarios.g_recursion import run_recursion
from edge_cases_pkg.scenarios.h_lambda_dynamic import apply_with_lambda, run_dynamic


def run_all() -> int:
    """Execute all scenarios in one flow."""
    total = 0
    total += alpha()
    total += call_cross_module()
    total += outer(2)

    playground = MethodPlayground()
    total += invoke_method_variants(playground)

    total += run_inheritance()
    total += run_decorator()
    total += run_async_sync()
    total += run_recursion()
    total += sum(apply_with_lambda([1, 2, 3]))
    total += run_dynamic()
    return total
