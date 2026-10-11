import os
from typing import Any, Dict, List, Optional, Tuple, Union
import pyodbc
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_PORT = os.environ.get("DB_PORT", "3306")
DB_NAME = os.environ.get("DB_NAME", "margin_db")
ODBC_DRIVER = os.environ.get("ODBC_DRIVER", "MySQL ODBC 8.0 Unicode Driver")


class DatabaseConnectionError(Exception):
    """Raised when establishing an ODBC database connection fails."""
    pass


def get_connection(username: str, password: str, role: Optional[str] = None) -> pyodbc.Connection:
    """
    Open an ODBC connection to MySQL as the specified user.
    Sets MAX_EXECUTION_TIME=30000 immediately if role is business_analyst.
    Raises DatabaseConnectionError on connection failure.
    """
    conn_str = (
        f"DRIVER={{{ODBC_DRIVER}}};"
        f"SERVER={DB_HOST};"
        f"PORT={DB_PORT};"
        f"DATABASE={DB_NAME};"
        f"UID={username};"
        f"PWD={password};"
        f"charset=utf8mb4;"
    )
    try:
        conn = pyodbc.connect(conn_str, timeout=10)
        if role == "business_analyst":
            cur = conn.cursor()
            cur.execute("SET SESSION max_execution_time=30000")
            cur.close()
        return conn
    except pyodbc.Error as exc:
        raise DatabaseConnectionError(f"Database connection failed for user {username}: {exc}") from exc


def call_sp(conn: pyodbc.Connection, sp_name: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """
    Call a stored procedure using ODBC {CALL sp_name(?, ...)} syntax.
    Drains any trailing result sets and commits the connection.
    Returns result rows as a list of dicts.
    """
    placeholders = ", ".join("?" for _ in (params or []))
    sql = f"{{CALL {sp_name}({placeholders})}}"
    cur = conn.cursor()
    try:
        if params:
            cur.execute(sql, tuple(params))
        else:
            cur.execute(sql)
        
        rows: List[Dict[str, Any]] = []
        if cur.description is not None:
            cols = [col[0] for col in cur.description]
            for row in cur.fetchall():
                rows.append(dict(zip(cols, list(row))))
        
        while cur.nextset():
            pass
        
        conn.commit()
        return rows
    finally:
        cur.close()


def call_sp_out(
    conn: pyodbc.Connection,
    sp_name: str,
    in_params: Optional[List[Any]] = None,
    out_count: int = 1
) -> Union[Any, Tuple[Any, ...]]:
    """
    Call a stored procedure with OUT parameters using {CALL sp_name(?, ..., @out0)} syntax.
    Drains intermediate result sets, selects the @out variables, and commits.
    Returns single value if out_count == 1, or tuple of values if out_count > 1.
    """
    in_placeholders = ["?" for _ in (in_params or [])]
    out_placeholders = [f"@out{i}" for i in range(out_count)]
    all_placeholders = in_placeholders + out_placeholders
    sql = f"{{CALL {sp_name}({', '.join(all_placeholders)})}}"
    cur = conn.cursor()
    try:
        if in_params:
            cur.execute(sql, tuple(in_params))
        else:
            cur.execute(sql)

        while cur.nextset():
            pass

        cur.execute(f"SELECT {', '.join(out_placeholders)}")
        out_row = cur.fetchone()
        conn.commit()

        if out_row is None:
            return None if out_count == 1 else tuple()
        
        res = list(out_row)
        if out_count == 1:
            return res[0]
        return tuple(res)
    finally:
        cur.close()
