"""Managing API tokens: list, create, revoke. Cookie sessions only.

- `GET /api/tokens/all` — every user's tokens, admin (Settings → Users).
- `GET /api/tokens` — your own tokens.
- `POST /api/tokens` — mint one; the response is the only one in the app
  that ever carries a raw token.
- `DELETE /api/tokens/{token_id}` — revoke your own; an admin revokes any.

A bearer token is no credential here: the bearer branch in `AuthMiddleware`
runs only under `/api/v1`. A refusal the user can correct answers 200
`{"ok": False, "message": …}`, the account routes' convention for `fetch()`
callers. Security events are logged after the write block closes (G3), and
never with the token or its prefix. Role first, then the `api` feature.
"""

import logging

from fastapi import APIRouter, Depends, Form, Request

from app.auth import require_role
from app.database import get_db
from app.features import require_feature
from app.services import api_tokens

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tokens")


# Declared before any dynamic sibling (G119).
@router.get("/all")
async def list_all_tokens(
    _=Depends(require_role("admin")),
    __=Depends(require_feature("api")),
):
    with get_db() as db:
        return {"ok": True, "tokens": api_tokens.list_all(db)}


@router.get("")
async def list_own_tokens(
    request: Request,
    _=Depends(require_role("viewer")),
    __=Depends(require_feature("api")),
):
    with get_db() as db:
        return {"ok": True, "tokens": api_tokens.list_for_user(db, request.state.user["id"])}


@router.post("")
async def mint_token(
    request: Request,
    name: str = Form(""),
    _=Depends(require_role("viewer")),
    __=Depends(require_feature("api")),
):
    user = request.state.user
    # Collapse every whitespace run (newlines included) to one space, and
    # refuse a name with nothing visible in it, such as zero-width spaces.
    name = " ".join(name.split())
    if not any(c.isprintable() and not c.isspace() for c in name):
        return {"ok": False, "message": "Give the token a name"}
    if len(name) > api_tokens.MAX_NAME:
        return {"ok": False,
                "message": f"Keep the name to {api_tokens.MAX_NAME} characters or fewer"}
    with get_db() as db:
        created = api_tokens.create(db, user["id"], name)
    if created is None:
        return {"ok": False,
                "message": f"You already have {api_tokens.MAX_PER_USER} tokens — revoke one first"}
    logger.info("API token %r created for user '%s'", name, user["username"])
    return {"ok": True, **created}


@router.delete("/{token_id}")
async def revoke_token(
    request: Request,
    token_id: int,
    _=Depends(require_role("viewer")),
    __=Depends(require_feature("api")),
):
    user = request.state.user
    owner = None if user["role"] == "admin" else user["id"]
    with get_db() as db:
        revoked = api_tokens.revoke(db, token_id, user_id=owner)
    if not revoked:
        return {"ok": False, "message": "Token not found"}
    logger.info("API token id=%d revoked by '%s'", token_id, user["username"])
    return {"ok": True}
