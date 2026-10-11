"""
tests/test_pct_time_property.py
Property 2: pct_time Sum Invariant
Validates: Requirement 7.1

Property:
For any query execution plan tree with node actual times and loop counts:
- root_total_ms = root_actual_ms * root_loops
- pct_time for each node = (actual_ms * loops) / root_total_ms * 100
- Root node pct_time is always 100.0%
- duration_ms of the query equals root_actual_ms (not root_total_ms)
"""

from dataclasses import dataclass
from typing import List, Optional
import pytest
from hypothesis import given, settings, strategies as st

@dataclass
class PlanNode:
    node_id: int
    parent_id: Optional[int]
    operation: str
    actual_time_ms: float
    loops: int

def compute_plan_telemetry(nodes: List[PlanNode]):
    """
    Computes query duration and pct_time per plan node.
    Root node has parent_id is None.
    """
    root_node = next(n for n in nodes if n.parent_id is None)
    root_actual_ms = root_node.actual_time_ms
    root_loops = root_node.loops
    root_total_ms = root_actual_ms * root_loops

    # Overall query duration is root_actual_ms (M from root node)
    duration_ms = root_actual_ms

    pct_times = {}
    for node in nodes:
        node_total_ms = node.actual_time_ms * node.loops
        pct_time = (node_total_ms / root_total_ms) * 100.0
        pct_times[node.node_id] = round(pct_time, 2)

    return duration_ms, pct_times

# Strategy generating arbitrary valid plan trees
@st.composite
def plan_tree_strategy(draw):
    # Root node
    root_actual = draw(st.floats(min_value=1.0, max_value=10000.0, allow_nan=False, allow_infinity=False))
    root_loops = draw(st.integers(min_value=1, max_value=10))
    nodes = [PlanNode(node_id=1, parent_id=None, operation="Root", actual_time_ms=root_actual, loops=root_loops)]

    # Child nodes (1 to 5 children)
    num_children = draw(st.integers(min_value=1, max_value=5))
    root_total = root_actual * root_loops
    
    for i in range(2, num_children + 2):
        parent_id = draw(st.sampled_from([n.node_id for n in nodes]))
        loops = draw(st.integers(min_value=1, max_value=100))
        # Child's total time bounded by root's total time
        child_total = draw(st.floats(min_value=0.01, max_value=root_total, allow_nan=False, allow_infinity=False))
        actual_time = child_total / loops
        nodes.append(PlanNode(node_id=i, parent_id=parent_id, operation=f"Op_{i}", actual_time_ms=actual_time, loops=loops))

    return nodes

@settings(max_examples=50)
@given(nodes=plan_tree_strategy())
def test_pct_time_invariant(nodes):
    duration_ms, pct_times = compute_plan_telemetry(nodes)
    
    root_node = next(n for n in nodes if n.parent_id is None)

    # 1. duration_ms equals root_actual_ms
    assert duration_ms == root_node.actual_time_ms

    # 2. Root node pct_time is always 100.0%
    assert abs(pct_times[root_node.node_id] - 100.0) < 0.01

    # 3. For every node, pct_time = (actual_ms * loops) / root_total_ms * 100 within tolerance
    root_total_ms = root_node.actual_time_ms * root_node.loops
    for node in nodes:
        expected_pct = (node.actual_time_ms * node.loops / root_total_ms) * 100.0
        assert abs(pct_times[node.node_id] - expected_pct) < 0.02

if __name__ == "__main__":
    pytest.main(["-v", __file__])
