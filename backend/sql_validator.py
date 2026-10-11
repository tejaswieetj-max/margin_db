"""
backend/sql_validator.py
SQL Console statement validator for Requirement 16.1 (Property 6).
Enforces:
- Single statement only (no multiple statements via semicolon)
- Read-only queries only: SELECT or WITH ... SELECT
- Rejects DML (INSERT, UPDATE, DELETE), DDL (DROP, ALTER, CREATE, TRUNCATE), and other modifying statements
"""

import re

def validate_sql_console_query(query: str) -> bool:
    if not query or not isinstance(query, str):
        return False
    
    # Remove single-line and multi-line comments
    cleaned = re.sub(r'(--[^\n]*)|(#[^\n]*)', '', query)
    cleaned = re.sub(r'/\*.*?\*/', '', cleaned, flags=re.DOTALL)
    cleaned = cleaned.strip()

    if not cleaned:
        return False

    # Remove single optional trailing semicolon
    if cleaned.endswith(';'):
        cleaned = cleaned[:-1].strip()

    # Reject if semicolon remains (multi-statement)
    if ';' in cleaned:
        return False

    upper_cleaned = cleaned.upper()

    # Must start with SELECT or WITH
    if not (upper_cleaned.startswith('SELECT') or upper_cleaned.startswith('WITH')):
        return False

    # If WITH clause, ensure it concludes with a SELECT statement
    if upper_cleaned.startswith('WITH'):
        if not re.search(r'\bSELECT\b', upper_cleaned):
            return False

    # Reject forbidden modifying statements
    forbidden_patterns = [
        r'\bINSERT\b', r'\bUPDATE\b', r'\bDELETE\b', r'\bDROP\b',
        r'\bALTER\b', r'\bTRUNCATE\b', r'\bREPLACE\b', r'\bCREATE\b',
        r'\bGRANT\b', r'\bREVOKE\b', r'\bCALL\b',
        r'\bINTO\s+OUTFILE\b', r'\bINTO\s+DUMPFILE\b'
    ]
    for pattern in forbidden_patterns:
        if re.search(pattern, upper_cleaned):
            return False

    return True
