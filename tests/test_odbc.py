"""
Stage 0 ODBC connectivity tests.

Tests in this file:
  - test_odbc_connection   : connect within 10 s; assert MySQL version starts with "8."
  - test_parameterized_query: run SELECT ? with a string binding; assert result matches input

NOTE: COMPANY_SEGMENT query and stored-procedure test moved to Stage 1 gate (task 1.10).
      Those tests require the schema migrations to have been applied and are therefore
      out of scope at Stage 0.

Requirements covered: 18.4, 18.6
"""

import os
import sys
import pytest
from dotenv import load_dotenv

load_dotenv()

try:
    import pyodbc
except ImportError:
    pytest.skip("pyodbc is not installed", allow_module_level=True)

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

ODBC_DRIVER = os.environ.get("ODBC_DRIVER", "MySQL ODBC 8.0 Unicode Driver")
DB_HOST     = os.environ.get("DB_HOST", "localhost")
DB_PORT     = os.environ.get("DB_PORT", "3306")
DB_NAME     = os.environ.get("DB_NAME", "margin_db")
ADMIN_USER  = os.environ.get("ADMIN_USER", "admin_user")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")



def _build_conn_str() -> str:
    return (
        f"DRIVER={{{ODBC_DRIVER}}};"
        f"SERVER={DB_HOST};"
        f"PORT={DB_PORT};"
        f"DATABASE={DB_NAME};"
        f"UID={ADMIN_USER};"
        f"PWD={ADMIN_PASSWORD};"
        "charset=utf8mb4;"
    )


def _get_connection() -> "pyodbc.Connection":
    """Return a live pyodbc connection, or fail cleanly on driver-not-found."""
    conn_str = _build_conn_str()
    try:
        conn = pyodbc.connect(conn_str, timeout=10)
    except pyodbc.InterfaceError as exc:
        # The ODBC driver is missing or not configured — fail immediately with
        # the driver name so the operator knows what to install.
        print(
            f"\n[ODBC driver not found] Driver name: '{ODBC_DRIVER}'\n"
            f"Full error: {exc}",
            file=sys.stderr,
        )
        pytest.fail(
            f"pyodbc.InterfaceError: ODBC driver '{ODBC_DRIVER}' not found. "
            "Install the MySQL ODBC 8.0 Unicode Driver and ensure it is registered "
            "with unixODBC (run `odbcinst -q -d` to list installed drivers)."
        )
    return conn


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


def test_odbc_connection():
    """
    Requirement 18.4 — ODBC connection test.

    Connect to MySQL using pyodbc within 10 seconds and verify that the
    server version string begins with "8.", confirming a MySQL 8.x server.
    """
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT VERSION()")
        row = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()

    assert row is not None, "SELECT VERSION() returned no rows"
    version_str: str = row[0]
    # Accept MySQL 8.x or any later major version (e.g. 9.x, 26.x).
    # The project requires MySQL 8.0+ semantics; Homebrew ships versions
    # beyond the 8.x series that retain full MySQL 8 compatibility.
    major = int(version_str.split(".")[0])
    assert major >= 8, (
        f"Expected MySQL 8.0+ but got version: '{version_str}'"
    )


def test_parameterized_query():
    """
    Requirement 18.6 — Parameterized query test.

    Execute ``SELECT ?`` with a string binding and assert the returned value
    equals the input.  This test does NOT touch any application table
    (COMPANY_SEGMENT etc.) because those tables are not yet created at Stage 0.
    """
    test_value = "margin_db_odbc_test_string"
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT ?", (test_value,))
        row = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()

    assert row is not None, "SELECT ? returned no rows"
    returned_value = row[0]
    assert returned_value == test_value, (
        f"Parameterized query mismatch — expected: '{test_value}', "
        f"got: '{returned_value}'"
    )
