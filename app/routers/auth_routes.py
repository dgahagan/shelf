from fastapi import APIRouter, Form, Request, Depends
from fastapi.responses import RedirectResponse, HTMLResponse

from app.auth import (
    hash_password, verify_password, create_token,
    set_auth_cookie, clear_auth_cookie, get_user_count,
    require_role,
)
from app.database import get_db

router = APIRouter()


# --- Public pages ---


@router.get("/login")
async def login_page(request: Request):
    user = getattr(request.state, "user", None)
    if user:
        return RedirectResponse(url="/browse", status_code=303)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    templates = request.app.state.templates
    with get_db() as db:
        user = db.execute(
            "SELECT id, username, password, role, display_name FROM users WHERE username = ?",
            (username,),
        ).fetchone()

    if not user or not verify_password(password, user["password"]):
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Invalid username or password"},
            status_code=401,
        )

    token = create_token(user["id"], user["username"], user["role"], user["display_name"])
    response = RedirectResponse(url="/browse", status_code=303)
    set_auth_cookie(response, token)
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=303)
    clear_auth_cookie(response)
    return response


# --- Setup wizard (only works when no users exist) ---


@router.get("/setup")
async def setup_page(request: Request):
    if get_user_count() > 0:
        return RedirectResponse(url="/login", status_code=303)
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "setup.html", {"error": None})


@router.post("/setup")
async def setup(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(""),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    if get_user_count() > 0:
        return RedirectResponse(url="/login", status_code=303)

    templates = request.app.state.templates

    username = username.strip()
    display_name = display_name.strip() or username

    if len(password) < 8:
        return templates.TemplateResponse(
            request, "setup.html",
            {"error": "Password must be at least 8 characters"},
        )
    if password != password_confirm:
        return templates.TemplateResponse(
            request, "setup.html",
            {"error": "Passwords do not match"},
        )
    if not username or len(username) < 2:
        return templates.TemplateResponse(
            request, "setup.html",
            {"error": "Username must be at least 2 characters"},
        )

    with get_db() as db:
        db.execute(
            "INSERT INTO users (username, password, display_name, role) VALUES (?, ?, ?, 'admin')",
            (username, hash_password(password), display_name),
        )
        user = db.execute("SELECT id, username, role, display_name FROM users WHERE username = ?", (username,)).fetchone()

    token = create_token(user["id"], user["username"], user["role"], user["display_name"])
    response = RedirectResponse(url="/browse", status_code=303)
    set_auth_cookie(response, token)
    return response


# --- User management (admin only) ---


@router.get("/api/users")
async def list_users(request: Request, _=Depends(require_role("admin"))):
    with get_db() as db:
        users = db.execute(
            "SELECT id, username, display_name, role, created_at FROM users ORDER BY created_at"
        ).fetchall()
    return [dict(u) for u in users]


@router.post("/api/users")
async def create_user(
    request: Request,
    username: str = Form(...),
    display_name: str = Form(""),
    password: str = Form(...),
    role: str = Form("viewer"),
    _=Depends(require_role("admin")),
):
    username = username.strip()
    display_name = display_name.strip() or username
    if role not in ("admin", "editor", "viewer"):
        role = "viewer"
    if len(password) < 8:
        return {"ok": False, "message": "Password must be at least 8 characters"}
    if not username or len(username) < 2:
        return {"ok": False, "message": "Username must be at least 2 characters"}

    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO users (username, password, display_name, role) VALUES (?, ?, ?, ?)",
                (username, hash_password(password), display_name, role),
            )
    except Exception:
        return {"ok": False, "message": "Username already exists"}

    return {"ok": True, "message": f"User '{username}' created"}


@router.post("/api/users/{user_id}/role")
async def update_user_role(
    request: Request,
    user_id: int,
    role: str = Form(...),
    _=Depends(require_role("admin")),
):
    if role not in ("admin", "editor", "viewer"):
        return {"ok": False, "message": "Invalid role"}

    current_user = request.state.user
    with get_db() as db:
        target = db.execute("SELECT id, role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            return {"ok": False, "message": "User not found"}

        # Prevent demoting the last admin
        if target["role"] == "admin" and role != "admin":
            admin_count = db.execute("SELECT COUNT(*) as cnt FROM users WHERE role = 'admin'").fetchone()["cnt"]
            if admin_count <= 1:
                return {"ok": False, "message": "Cannot demote the last admin"}

        db.execute(
            "UPDATE users SET role = ?, updated_at = datetime('now') WHERE id = ?",
            (role, user_id),
        )

    return {"ok": True, "message": "Role updated"}


@router.post("/api/users/{user_id}/password")
async def reset_user_password(
    request: Request,
    user_id: int,
    password: str = Form(...),
    _=Depends(require_role("admin")),
):
    if len(password) < 8:
        return {"ok": False, "message": "Password must be at least 8 characters"}

    with get_db() as db:
        target = db.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            return {"ok": False, "message": "User not found"}
        db.execute(
            "UPDATE users SET password = ?, updated_at = datetime('now') WHERE id = ?",
            (hash_password(password), user_id),
        )

    return {"ok": True, "message": "Password updated"}


@router.post("/api/account/password")
async def change_own_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    _=Depends(require_role("viewer")),
):
    """Any authenticated user can change their own password."""
    user = request.state.user
    if len(new_password) < 8:
        return {"ok": False, "message": "New password must be at least 8 characters"}

    with get_db() as db:
        row = db.execute("SELECT password FROM users WHERE id = ?", (user["id"],)).fetchone()
        if not row or not verify_password(current_password, row["password"]):
            return {"ok": False, "message": "Current password is incorrect"}
        db.execute(
            "UPDATE users SET password = ?, updated_at = datetime('now') WHERE id = ?",
            (hash_password(new_password), user["id"]),
        )

    return {"ok": True, "message": "Password changed"}


@router.post("/api/account/display-name")
async def change_display_name(
    request: Request,
    display_name: str = Form(...),
    _=Depends(require_role("viewer")),
):
    """Any authenticated user can update their own display name."""
    user = request.state.user
    display_name = display_name.strip()
    if not display_name:
        return {"ok": False, "message": "Display name cannot be empty"}

    with get_db() as db:
        db.execute(
            "UPDATE users SET display_name = ?, updated_at = datetime('now') WHERE id = ?",
            (display_name, user["id"]),
        )

    # Refresh the JWT so the nav bar updates immediately
    token = create_token(user["id"], user["username"], user["role"], display_name)
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"ok": True, "message": "Display name updated", "display_name": display_name})
    set_auth_cookie(resp, token)
    return resp


@router.delete("/api/users/{user_id}")
async def delete_user(request: Request, user_id: int, _=Depends(require_role("admin"))):
    current_user = request.state.user
    if current_user["id"] == user_id:
        return {"ok": False, "message": "Cannot delete your own account"}

    with get_db() as db:
        target = db.execute("SELECT id, role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            return {"ok": False, "message": "User not found"}

        if target["role"] == "admin":
            admin_count = db.execute("SELECT COUNT(*) as cnt FROM users WHERE role = 'admin'").fetchone()["cnt"]
            if admin_count <= 1:
                return {"ok": False, "message": "Cannot delete the last admin"}

        db.execute("DELETE FROM users WHERE id = ?", (user_id,))

    return {"ok": True, "message": "User deleted"}
