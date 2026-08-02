"""Optional auto-publish: push the generated site to a static host each run.

Netlify is used because its deploy API is a single authenticated HTTP POST of
a zip file — no CLI, no build step, free tier. One-time setup:

  1. netlify.com -> Add new site -> Deploy manually (drag the site/ folder
     once; this creates the site and its URL).
  2. Site configuration -> copy the Site ID (API ID).
  3. User settings -> Applications -> New access token.
  4. Put both in .env (or the CI secrets):
       NETLIFY_SITE_ID=...
       NETLIFY_AUTH_TOKEN=...

Every subsequent pipeline run then republishes automatically; viewers just
refresh the same URL. Remove the env vars to turn it off.
"""
import io
import logging
import os
import zipfile
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DEPLOY_TIMEOUT = 120


def publish(site_dir: Path) -> str | None:
    """Deploy site_dir to Netlify if configured. Returns the live URL."""
    token = os.environ.get("NETLIFY_AUTH_TOKEN")
    site_id = os.environ.get("NETLIFY_SITE_ID")
    if not token or not site_id:
        return None

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(site_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(site_dir))
    buffer.seek(0)

    response = requests.post(
        f"https://api.netlify.com/api/v1/sites/{site_id}/deploys",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/zip"},
        data=buffer.read(),
        timeout=DEPLOY_TIMEOUT,
    )
    response.raise_for_status()
    url = response.json().get("ssl_url") or response.json().get("url", "")
    logger.info("Published site to %s", url)
    return url
