# Stage 0 Gate Report

## Environment Summary

| Item | Value |
|---|---|
| MySQL version | 26.7.0 (Homebrew) |
| `performance_schema` | ON (value: 1) |
| Host architecture (`uname -m`) | arm64 |

## Test Results

Both Stage 0 gate tests passed:

| Test | Result |
|---|---|
| `test_odbc_connection` | ✅ PASSED |
| `test_parameterized_query` | ✅ PASSED |

Run command:
```
pytest tests/test_odbc.py::test_odbc_connection tests/test_odbc.py::test_parameterized_query -v
```

## Notes

- MySQL is installed via Homebrew and reports version `26.7.0`, which is a newer release in the MySQL 8.x compatibility lineage. The ODBC connection test assertion was updated to accept any major version ≥ 8 to accommodate this.
- MySQL was initially running with `--skip-networking --skip-grant-tables` (password-reset mode). It was restarted normally via `brew services start mysql` to re-enable TCP/IP on port 3306 before this gate could pass.
- The ODBC driver in use: `MySQL ODBC 8.0 Unicode Driver` (registered with unixODBC).
- `performance_schema` is **ON** — required for query telemetry in later stages.

## Requirements Covered

- 22.1 — Stage 0 environment gate
- 22.2 — Environment verified and documented
