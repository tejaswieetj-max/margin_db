import os
import re
import time
from typing import Dict, List, Optional, Tuple
import jwt
from fastapi import APIRouter, Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from dotenv import load_dotenv

from backend.db import DatabaseConnectionError, get_connection
from backend.models.requests import LoginRequest
from backend.models.responses import LoginResponse

load_dotenv()

JWT_SECRET = os.environ.get("JWT_SECRET", "default_jwt_secret")
ALGORITHM = "HS256"
TOKEN_EXPIRY_SECONDS = 3600

# In-memory credential map: token -> (username, password, role)
# Never persisted to disk or logs
credential_map: Dict[str, Tuple[str, str, str]] = {}

security_scheme = HTTPBearer(auto_error=False)
router = APIRouter(prefix="/auth", tags=["auth"])


def _extract_role_from_connection(conn) -> Optional[str]:
    cur = conn.cursor()
    try:
        cur.execute("SELECT CURRENT_ROLE()")
        row = cur.fetchone()
        raw_role = row[0] if row else None
        
        if raw_role and raw_role.upper() != "NONE" and raw_role.strip():
            # e.g. `business_analyst`@`%` or business_analyst
            match = re.match(r"`?([a-zA-Z0-9_]+)`?(@.*)?", raw_role.strip())
            if match:
                role = match.group(1)
                if role.lower() != "none":
                    return role

        # Fallback to SHOW GRANTS
        cur.execute("SHOW GRANTS")
        grants = [g[0] for g in cur.fetchall()]
        for g in grants:
            grant_match = re.search(r"GRANT\s+`?([a-zA-Z0-9_]+)`?@`?%?`?\s+TO", g)
            if grant_match:
                role = grant_match.group(1)
                if role in ("business_analyst", "db_engineer", "role_telemetry"):
                    return role
        return None
    finally:
        cur.close()


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest):
    """
    Authenticate user with database credentials, retrieve active role,
    and return signed JWT token.
    """
    try:
        conn = get_connection(req.username, req.password)
    except DatabaseConnectionError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid database credentials"
        )

    try:
        role = _extract_role_from_connection(conn)
        if not role:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User has no assigned role"
            )
    finally:
        conn.close()

    now = int(time.time())
    payload = {
        "sub": req.username,
        "role": role,
        "exp": now + TOKEN_EXPIRY_SECONDS,
        "iat": now,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=ALGORITHM)
    credential_map[token] = (req.username, req.password, role)

    return LoginResponse(token=token, role=role, username=req.username)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_scheme),
) -> Tuple[str, str, str]:
    """
    Validate JWT token, verify signature & expiry, and retrieve credentials from in-memory map.
    Returns (username, password, role).
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header"
        )

    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired"
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )

    creds = credential_map.get(token)
    if not creds:
        # If server restarted or token missing from map, sub and role exist in token
        # but password cannot be reconstructed
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired, please log in again"
        )
    return creds


def require_role(allowed_roles: List[str]):
    def role_checker(user_tuple: Tuple[str, str, str] = Depends(get_current_user)) -> Tuple[str, str, str]:
        username, password, role = user_tuple
        if role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires role in {allowed_roles}, but caller has role '{role}'"
            )
        return user_tuple
    return role_checker
