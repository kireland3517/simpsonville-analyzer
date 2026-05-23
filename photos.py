"""
photos.py
─────────
Google Photos OAuth 2.0 integration.
Credentials are loaded from the GOOGLE_CREDENTIALS_JSON environment variable
(a JSON string) if set, otherwise falls back to google_credentials.json on disk.

Token lookup order (fastest → slowest):
  1. _token_cache       — in-memory, set after exchange_code() or a successful load
  2. Supabase           — oauth_tokens table (when SUPABASE_URL is set)
  3. GOOGLE_TOKEN_JSON  — env var JSON string, useful for seeding an existing token
  4. google_token.json  — local development fallback

Token save order (all destinations that are available are written):
  1. _token_cache  (always)
  2. Supabase      (when SUPABASE_URL + SUPABASE_SERVICE_KEY are set)
  3. google_token.json (best-effort, local dev)

Supabase table schema:
    CREATE TABLE oauth_tokens (
        id         TEXT PRIMARY KEY,   -- always "google"
        token_data JSONB NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now()
    );

No files are uploaded — read-only access only.

Scopes used:
    https://www.googleapis.com/auth/photoslibrary.readonly
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Optional

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

# Optional Supabase support — graceful fallback if not installed or not configured
try:
    from supabase import create_client as _create_supabase_client
    _SUPABASE_AVAILABLE = True
except ImportError:
    _SUPABASE_AVAILABLE = False

# ─── Config ───────────────────────────────────────────────────────────────────

TOKEN_FILE = Path("google_token.json")
SUPABASE_TOKEN_ID = "google"           # primary key used in oauth_tokens table

SCOPES = [
    "https://www.googleapis.com/auth/photoslibrary.readonly",
    "https://www.googleapis.com/auth/photoslibrary.sharing",
]

PHOTOS_API_BASE = "https://photoslibrary.googleapis.com/v1"

# Holds the PKCE verifier generated in get_auth_url() so exchange_code()
# can use it in the same server process without passing it through the browser.
_pkce_verifier: Optional[str] = None

# In-memory token cache — survives for the lifetime of the server process.
# Populated by exchange_code() and get_credentials() on first successful load.
_token_cache: Optional[dict] = None


# ─── Supabase helpers ─────────────────────────────────────────────────────────

def _supabase_client():
    """Return a Supabase client if both env vars are set, else None."""
    if not _SUPABASE_AVAILABLE:
        return None
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if url and key:
        return _create_supabase_client(url, key)
    return None


def _save_token_to_supabase(token_data: dict) -> None:
    """Upsert token_data into the oauth_tokens table. Silently ignores errors."""
    client = _supabase_client()
    if client is None:
        return
    try:
        client.table("oauth_tokens").upsert({
            "id":         SUPABASE_TOKEN_ID,
            "token_data": token_data,
        }).execute()
    except Exception:
        pass


def _load_token_from_supabase() -> Optional[dict]:
    """Fetch token_data from the oauth_tokens table. Returns None on any error."""
    client = _supabase_client()
    if client is None:
        return None
    try:
        result = (
            client.table("oauth_tokens")
            .select("token_data")
            .eq("id", SUPABASE_TOKEN_ID)
            .single()
            .execute()
        )
        return result.data.get("token_data") if result.data else None
    except Exception:
        return None


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _load_client_config() -> dict:
    """
    Load OAuth client config from the GOOGLE_CREDENTIALS_JSON env var (a JSON
    string) if set, otherwise read google_credentials.json from disk.
    Returns the full JSON including the "web" wrapper as Flow.from_client_config() expects.
    """
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if creds_json:
        return json.loads(creds_json)
    with open("google_credentials.json") as f:
        return json.load(f)


def _build_flow() -> Flow:
    redirect_uri = os.environ.get("REDIRECT_URI", "http://localhost:8000/auth/callback")
    return Flow.from_client_config(
        _load_client_config(),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )


# ─── Public functions ─────────────────────────────────────────────────────────

def get_auth_url() -> str:
    """
    Build and return the Google OAuth authorization URL with PKCE.
    Generates a fresh code_verifier each call and stores it module-level
    so exchange_code() can include it in the token request.
    """
    global _pkce_verifier

    # Generate a cryptographically random 64-char URL-safe verifier
    _pkce_verifier = secrets.token_urlsafe(64)

    # SHA-256 hash → base64url-encode (no padding) → code_challenge
    digest = hashlib.sha256(_pkce_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    flow = _build_flow()
    print(f"[photos] get_auth_url() requesting scopes: {SCOPES}", flush=True)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",               # force refresh_token every time
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    return auth_url


def _save_token(token_data: dict) -> None:
    """
    Persist token_data to all available destinations:
      1. _token_cache  (always)
      2. Supabase      (when configured)
      3. google_token.json (best-effort, local dev)
    """
    global _token_cache
    _token_cache = token_data
    _save_token_to_supabase(token_data)
    try:
        TOKEN_FILE.write_text(json.dumps(token_data, indent=2), encoding="utf-8")
    except OSError:
        pass


def exchange_code(code: str) -> dict:
    """
    Exchange an authorization code (from the OAuth callback) for tokens.
    Includes the PKCE code_verifier generated by get_auth_url().
    Persists the token via _save_token() (Supabase + local file).
    """
    flow = _build_flow()
    flow.fetch_token(code=code, code_verifier=_pkce_verifier)
    creds = flow.credentials

    token_data = {
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        list(creds.scopes or SCOPES),
    }

    _save_token(token_data)
    return token_data


def _load_token_data() -> Optional[dict]:
    """
    Load raw token data using the priority chain:
      1. _token_cache       (in-memory)
      2. Supabase           (oauth_tokens table)
      3. GOOGLE_TOKEN_JSON  (env var JSON string)
      4. google_token.json  (local dev file)
    Populates _token_cache from whichever source succeeds.
    Returns None if no source has a token.
    """
    global _token_cache

    if _token_cache:
        return _token_cache

    supabase_data = _load_token_from_supabase()
    if supabase_data:
        _token_cache = supabase_data
        return _token_cache

    env_token = os.environ.get("GOOGLE_TOKEN_JSON")
    if env_token:
        _token_cache = json.loads(env_token)
        return _token_cache

    if TOKEN_FILE.exists():
        _token_cache = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        return _token_cache

    return None


def get_credentials() -> Optional[Credentials]:
    """
    Return valid Google credentials using the four-source priority chain.
    Refreshes the access token if expired and re-persists via _save_token().
    Returns None if no token is found (user must authenticate first).
    """
    data = _load_token_data()
    if data is None:
        return None

    saved_scopes = data.get("scopes", SCOPES)
    print(f"[photos] get_credentials() token scopes: {saved_scopes}", flush=True)

    creds = Credentials(
        token=data.get("token"),
        refresh_token=data.get("refresh_token"),
        token_uri=data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=data.get("client_id"),
        client_secret=data.get("client_secret"),
        scopes=saved_scopes,
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        data["token"] = creds.token
        _save_token(data)
        print(f"[photos] token refreshed, scopes still: {saved_scopes}", flush=True)

    return creds


def list_album_photos(album_id: str, credentials: Credentials) -> list[dict]:
    """
    Return all photos in the given Google Photos album.
    Handles pagination automatically.

    Each item dict contains:
        id, filename, mimeType, baseUrl, productUrl
    """
    results: list[dict] = []
    page_token: Optional[str] = None

    headers = {"Authorization": f"Bearer {credentials.token}"}

    while True:
        payload: dict = {"albumId": album_id, "pageSize": 100}
        if page_token:
            payload["pageToken"] = page_token

        try:
            response = requests.post(
                f"{PHOTOS_API_BASE}/mediaItems:search",
                headers=headers,
                json=payload,
                timeout=30,
            )
            print(f"[photos-debug] list_album_photos status: {response.status_code}", flush=True)
            print(f"[photos-debug] list_album_photos response JSON: {response.text[:2000]}", flush=True)
            response.raise_for_status()
        except requests.RequestException as exc:
            print(f"[photos-debug] list_album_photos request failed: {exc}", flush=True)
            return results

        body = response.json()
        page_items = body.get("mediaItems", [])
        print(f"[photos-debug] list_album_photos page items: {len(page_items)}, running total: {len(results) + len(page_items)}", flush=True)
        for item in page_items:
            results.append({
                "id":         item.get("id"),
                "filename":   item.get("filename"),
                "mimeType":   item.get("mimeType"),
                "baseUrl":    item.get("baseUrl"),
                "productUrl": item.get("productUrl"),
            })

        page_token = body.get("nextPageToken")
        if not page_token:
            break

    return results


def get_photo_bytes(
    base_url: str,
    credentials: Credentials,
    width: int = 400,
) -> Optional[bytes]:
    """
    Download image bytes from a Google Photos baseUrl.

    width=400  → appends "=w400-h400" for a thumbnail
    width=0    → appends "=d" for the full-resolution original download
    """
    if width > 0:
        url = f"{base_url}=w{width}-h{width}"
    else:
        url = f"{base_url}=d"

    headers = {"Authorization": f"Bearer {credentials.token}"}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.content
    except requests.RequestException:
        return None
