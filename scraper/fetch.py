"""HTTP fetching with retries, a polite delay, and a response-size cap."""
import logging
import time

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; compete-monitor/1.0)"
REQUEST_DELAY_SECONDS = 1.0
MAX_RETRIES = 3
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB — protect against runaway pages

_last_request_at = 0.0


def fetch(url: str, timeout: float = 30.0,
          max_bytes: int = MAX_RESPONSE_BYTES) -> str:
    """Fetch a URL and return its body text, retrying on transient errors.

    max_bytes can be raised for sources that are legitimately large (e.g.
    Trailhead's 18 MB content sitemap).
    """
    global _last_request_at

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        elapsed = time.monotonic() - _last_request_at
        if elapsed < REQUEST_DELAY_SECONDS:
            time.sleep(REQUEST_DELAY_SECONDS - elapsed)
        _last_request_at = time.monotonic()

        try:
            response = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
                stream=True,
            )
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > max_bytes:
                    logger.warning("Truncating oversized response from %s", url)
                    break
                chunks.append(chunk)
            # Trust the header's encoding only when it actually declared one;
            # requests defaults charset-less text/* to ISO-8859-1, which
            # mojibakes the (in practice) UTF-8 content of e.g. the
            # Salesforce blog feed.
            content_type = response.headers.get("Content-Type", "")
            if "charset" in content_type.lower():
                encoding = response.encoding or "utf-8"
            else:
                encoding = "utf-8"
            return b"".join(chunks).decode(encoding, errors="replace")
        except requests.RequestException as e:
            last_error = e
            logger.warning("Fetch attempt %d/%d failed for %s: %s",
                           attempt, MAX_RETRIES, url, e)
            if attempt < MAX_RETRIES:
                time.sleep(2**attempt)

    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts: {last_error}")
