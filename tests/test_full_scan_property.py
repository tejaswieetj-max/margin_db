"""
tests/test_full_scan_property.py
Property 3: Full-Scan Flag Rule Completeness
Validates: Requirements 7.4, 11.2

Rule:
A query execution is flagged if and only if at least one plan node has:
operation.startswith("Table scan on") AND pct_time > 50
"""

import pytest
from hypothesis import given, settings, strategies as st
from backend.telemetry import is_query_flagged

# Strategies for plan nodes
table_scan_operations = st.sampled_from([
    "Table scan on AZURE_VM",
    "Table scan on azure_vm",
    "Table scan on ORDERS",
    "Table scan on FACT_SALES",
])

non_table_scan_operations = st.sampled_from([
    "Index range scan on AZURE_VM using idx_1",
    "Index lookup on AZURE_VM",
    "Group aggregate: avg(cpu)",
    "Filter: (category = 'Delay-insensitive')",
    "Sort: created_at ASC",
    "Nested loop inner join",
    "Table scan_foo on OTHER",  # does not start with "Table scan on"
])

pct_times = st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)

plan_node_strategy = st.fixed_dictionaries({
    "operation": st.one_of(table_scan_operations, non_table_scan_operations),
    "pct_time": pct_times,
})

plan_trees_strategy = st.lists(plan_node_strategy, min_size=1, max_size=20)


@settings(max_examples=100)
@given(nodes=plan_trees_strategy)
def test_full_scan_flag_rule_completeness(nodes):
    flagged = is_query_flagged(nodes)

    # Reference condition
    has_qualifying_node = any(
        node["operation"].startswith("Table scan on") and node["pct_time"] > 50.0
        for node in nodes
    )

    # Assert exact equivalence: no false positives and no false negatives
    if has_qualifying_node:
        assert flagged is True, "Expected flagged=True for qualifying table scan node"
    else:
        assert flagged is False, "Expected flagged=False when no qualifying table scan node exists"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
