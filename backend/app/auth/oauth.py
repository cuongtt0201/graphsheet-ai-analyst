import os
import secrets

# This Google OAuth Client has previously been granted broader scopes (drive,
# gmail.modify, calendar, ...) under other projects/consents on this account.
# With include_granted_scopes dropped below we no longer ask for that, but
# Google may still echo back a superset on the token response; oauthlib treats
# any scope mismatch as a hard error unless this is relaxed. We store whatever
# scopes actually came back (see exchange_code) rather than assuming our
# originally requested list, so relaxing this check is safe.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

from google_auth_oauthlib.flow import Flow  # noqa: E402

from app.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, GOOGLE_SCOPES  # noqa: E402

_CLIENT_CONFIG = {
    "web": {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [GOOGLE_REDIRECT_URI],
    }
}


def _new_flow(code_verifier: str) -> Flow:
    flow = Flow.from_client_config(_CLIENT_CONFIG, scopes=GOOGLE_SCOPES)
    flow.redirect_uri = GOOGLE_REDIRECT_URI
    flow.code_verifier = code_verifier
    return flow


def build_auth_url() -> tuple[str, str]:
    """Returns (auth_url, code_verifier). We pass the code_verifier
    directly inside the state parameter so that the callback endpoint
    can retrieve it statelessly, bypassing proxy/session cookie loss issues."""
    code_verifier = secrets.token_urlsafe(64)
    flow = _new_flow(code_verifier)
    auth_url, _state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=code_verifier,
    )
    return auth_url, code_verifier


def exchange_code(code: str, code_verifier: str) -> dict:
    """Returns a plain dict of credential fields, safe to store in the signed
    session cookie (token, refresh_token, token_uri, client_id, client_secret,
    scopes) - this is the user's own OAuth grant, not a shared secret."""
    flow = _new_flow(code_verifier)
    flow.fetch_token(code=code)
    creds = flow.credentials
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }


def credentials_from_session(creds_dict: dict):
    from google.oauth2.credentials import Credentials

    return Credentials(**creds_dict)
