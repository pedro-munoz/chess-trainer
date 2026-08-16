"""Minimal GitHub Gist client for cross-device sync.

The gist holds one JSON file per device (see sync_state); this module only
knows how to read them all and write one. Credentials come from
``data/gist_auth.json`` — which is gitignored, unlike config.toml — or from the
environment, so a token never reaches the repository.

The token has to be a *classic* PAT with the ``gist`` scope: fine-grained
tokens still cannot reach gists, and the scope cannot be narrowed to a single
gist. Treat it as it is: a credential for every gist on the account.
"""

import json
import os
from pathlib import Path

import requests

from . import DATA_DIR

API = "https://api.github.com"
AUTH_PATH = DATA_DIR / "gist_auth.json"
TIMEOUT = 30

# Matching web/js/sync.js — the same files, read by the same rules.
KIND = "chess-trainer-sync"
VERSION = 1


class GistError(RuntimeError):
    pass


def load_auth(path: Path | None = None) -> tuple[str, str]:
    """Return (gist_id, token). Environment wins over the file."""
    gist_id = os.environ.get("CHESS_TRAINER_GIST_ID", "")
    token = os.environ.get("CHESS_TRAINER_GIST_TOKEN", "")
    path = path or AUTH_PATH
    if (not gist_id or not token) and path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        gist_id = gist_id or data.get("gist_id", "")
        token = token or data.get("token", "")
    if not gist_id or not token:
        raise GistError(
            f"no gist credentials. Write {path} as "
            '{"gist_id": "...", "token": "..."} or set CHESS_TRAINER_GIST_ID '
            "and CHESS_TRAINER_GIST_TOKEN."
        )
    return gist_id, token


def _headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {token}",
    }


def _check(resp: requests.Response) -> None:
    if resp.ok:
        return
    if resp.status_code == 401:
        raise GistError("token rejected (401) — expired, or not a classic token")
    if resp.status_code == 404:
        raise GistError(
            "gist not found (404) — check the id, and that the token is a "
            "classic token carrying the gist scope"
        )
    if resp.status_code in (403, 429):
        raise GistError(f"rate limited ({resp.status_code}) — retry in a few minutes")
    raise GistError(f"GitHub returned {resp.status_code}: {resp.text[:200]}")


def read_files(gist_id: str, token: str) -> dict[str, dict]:
    """Every ``state-*.json`` payload in the gist, keyed by filename."""
    resp = requests.get(f"{API}/gists/{gist_id}", headers=_headers(token), timeout=TIMEOUT)
    _check(resp)
    out = {}
    for name, meta in (resp.json().get("files") or {}).items():
        if not (name.startswith("state-") and name.endswith(".json")):
            continue
        content = meta.get("content")
        # Files past ~1 MB come back with the content elided.
        if meta.get("truncated") or content is None:
            raw = requests.get(meta["raw_url"], timeout=TIMEOUT)
            _check(raw)
            content = raw.text
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise GistError(f"{name} is not valid JSON: {exc}") from exc
        if payload.get("kind") != KIND:
            continue
        if payload.get("version", 0) > VERSION:
            raise GistError(f"{name} was written by a newer version of the trainer")
        out[name] = payload
    return out


def write_file(gist_id: str, token: str, filename: str, payload: dict) -> None:
    body = {"files": {filename: {"content": json.dumps(payload, indent=1)}}}
    resp = requests.patch(f"{API}/gists/{gist_id}", headers=_headers(token),
                          json=body, timeout=TIMEOUT)
    _check(resp)
