"""
tests/test_sql_console_rejection_property.py
Property 6: SQL Console Multi-Statement Rejection
Validates: Requirement 16.1

Property:
- Grammar-based valid SELECT and WITH ... SELECT queries are ACCEPTED (True).
- Multi-statement queries (semicolon followed by non-whitespace/additional statements) are REJECTED (False).
- DML and DDL statements (INSERT, UPDATE, DELETE, DROP, ALTER, etc.) are REJECTED (False).
"""

import sys
from pathlib import Path
import pytest
from hypothesis import given, settings, strategies as st

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.sql_validator import validate_sql_console_query

# Strategies for valid components
identifier_st = st.from_regex(r'^[a-z_][a-z0-9_]{1,15}$', fullmatch=True)
columns_st = st.lists(identifier_st, min_size=1, max_size=4).map(lambda cols: ", ".join(cols))
tables_st = st.sampled_from(["v_sales", "v_financials", "v_events", "v_my_queries", "AZURE_VM", "v_partitions"])

@st.composite
def valid_select_query_strategy(draw):
    cols = draw(columns_st)
    tbl = draw(tables_st)
    has_where = draw(st.booleans())
    query = f"SELECT {cols} FROM {tbl}"
    if has_where:
        col = draw(identifier_st)
        val = draw(st.integers(min_value=1, max_value=1000))
        query += f" WHERE {col} = {val}"
    
    # Optional trailing semicolon
    if draw(st.booleans()):
        query += ";"
    return query

@st.composite
def valid_with_query_strategy(draw):
    cte_name = draw(identifier_st)
    cols = draw(columns_st)
    tbl = draw(tables_st)
    return f"WITH {cte_name} AS (SELECT {cols} FROM {tbl}) SELECT * FROM {cte_name};"

@st.composite
def multi_statement_strategy(draw):
    stmt1 = draw(valid_select_query_strategy()).rstrip(';')
    stmt2 = draw(st.sampled_from([
        "SELECT 1",
        "SELECT * FROM v_sales",
        "DROP TABLE t",
        "INSERT INTO t VALUES (1)",
        "SELECT 'a'"
    ]))
    return f"{stmt1}; {stmt2}"

@st.composite
def dml_ddl_statement_strategy(draw):
    verb = draw(st.sampled_from(["INSERT INTO", "UPDATE", "DELETE FROM", "DROP TABLE", "ALTER TABLE", "TRUNCATE", "CREATE TABLE"]))
    tbl = draw(identifier_st)
    return f"{verb} {tbl} VALUES (1);"

@settings(max_examples=50)
@given(query=valid_select_query_strategy())
def test_valid_select_accepted(query):
    assert validate_sql_console_query(query) is True

@settings(max_examples=50)
@given(query=valid_with_query_strategy())
def test_valid_with_accepted(query):
    assert validate_sql_console_query(query) is True

@settings(max_examples=50)
@given(query=multi_statement_strategy())
def test_multi_statement_rejected(query):
    assert validate_sql_console_query(query) is False

@settings(max_examples=50)
@given(query=dml_ddl_statement_strategy())
def test_dml_ddl_rejected(query):
    assert validate_sql_console_query(query) is False

if __name__ == "__main__":
    pytest.main(["-v", __file__])
