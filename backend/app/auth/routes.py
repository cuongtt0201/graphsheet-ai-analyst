from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.auth.oauth import build_auth_url, credentials_from_session, exchange_code
from app.config import FRONTEND_URL
from app.memory import graph

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
def login(request: Request):
    auth_url, code_verifier = build_auth_url()
    request.session["code_verifier"] = code_verifier
    return RedirectResponse(auth_url)


@router.get("/google/callback")
def callback(request: Request, code: str, state: str):
    code_verifier = state
    creds_dict = exchange_code(code, code_verifier)
    request.session["google_credentials"] = creds_dict

    creds = credentials_from_session(creds_dict)
    from googleapiclient.discovery import build

    userinfo = build("oauth2", "v2", credentials=creds).userinfo().get().execute()
    email = userinfo.get("email")
    request.session["email"] = email

    # Graph identity: email is the stable key, so a returning user's memory
    # (habits/recipes/files) is recognized regardless of session/browser.
    graph.merge_user(email, email=email, name=userinfo.get("name"))

    return RedirectResponse(FRONTEND_URL)


@router.get("/me")
def me(request: Request):
    email = request.session.get("email")
    if not email:
        return {"authenticated": False}
    return {"authenticated": True, "email": email}


@router.post("/mock-login")
def mock_login(request: Request, body: dict):
    email = body.get("email")
    if not email:
        return {"error": "Email is required"}
    request.session["email"] = email
    graph.merge_user(email, email=email, name=email.split("@")[0])
    return {"authenticated": True, "email": email}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}
