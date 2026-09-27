"""Downloads, mirrors and release metadata - standard library only.

Everything the bootstrapper fetches goes through :class:`Downloader` so that
failures are uniform: partial file kept for a resume, curl/wget used as a
fallback transport when urllib is blocked, and a clear NetError message.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from . import util

USER_AGENT = "cpp-setup/1.0 (+https://github.com/; cross-platform C++ bootstrapper)"
GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"
GITHUB_HOST = "https://github.com"
TIMEOUT = 45


class NetError(Exception):
    """Raised when a resource could not be downloaded or queried."""


def _request(url: str, headers: Optional[dict] = None, timeout: int = TIMEOUT):
    request = urllib.request.Request(url)
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept-Encoding", "identity")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    return urllib.request.urlopen(request, timeout=timeout)  # nosec - fixed, allowlisted hosts


def fetch_text(url: str, timeout: int = TIMEOUT) -> str:
    """GET a URL and return the body as text (used for JSON endpoints)."""
    try:
        with _request(url, timeout=timeout) as response:
            raw = response.read()
        return raw.decode("utf-8", "replace")
    except Exception as exc:
        raise NetError("GET %s failed: %s" % (url, exc))


def fetch_json(url: str, timeout: int = TIMEOUT) -> dict:
    try:
        return json.loads(fetch_text(url, timeout=timeout))
    except ValueError as exc:
        raise NetError("GET %s returned invalid JSON: %s" % (url, exc))


def github_release(repo: str, timeout: int = TIMEOUT) -> dict:
    """Latest release metadata for ``owner/name`` (empty dict on failure)."""
    url = GITHUB_API.format(repo=repo)
    try:
        data = fetch_json(url, timeout=timeout)
    except NetError as exc:
        raise NetError("GitHub API unreachable for %s (%s)." % (repo, exc))
    if not isinstance(data, dict) or "assets" not in data:
        raise NetError("Unexpected GitHub API payload for %s." % repo)
    return data


def release_assets(release: dict) -> List[dict]:
    assets = []
    for asset in release.get("assets") or []:
        name = asset.get("name") or ""
        if not name:
            continue
        assets.append(
            {
                "name": name,
                "size": int(asset.get("size") or 0),
                "url": asset.get("browser_download_url")
                or "%s/%s/releases/download/%s/%s" % (GITHUB_HOST, "", release.get("tag_name", ""), name),
                "tag": release.get("tag_name", ""),
            }
        )
    return assets


def direct_url(repo: str, tag: str, asset_name: str) -> str:
    """Stable ``releases/latest/download`` URL that skips the API entirely."""
    if tag and tag != "latest":
        return "%s/%s/releases/download/%s/%s" % (GITHUB_HOST, repo, tag, asset_name)
    return "%s/%s/releases/latest/download/%s" % (GITHUB_HOST, repo, asset_name)


def select_asset(assets: Sequence[dict], patterns: Sequence[str], label: str = "") -> Optional[dict]:
    """Pick the best asset for this machine.

    ``patterns`` are regexes tried in order (earlier = preferred).  Within a
    pattern the newest-looking name wins, so releases that keep a stable
    naming scheme still resolve after an update.
    """
    for pattern in patterns:
        matcher = re.compile(pattern, re.IGNORECASE)
        matches = [asset for asset in assets if matcher.search(asset.get("name", ""))]
        if matches:
            matches.sort(key=lambda asset: _asset_sort_key(asset["name"]), reverse=True)
            return matches[0]
    return None


def _asset_sort_key(name: str):
    parts = []
    for token in re.findall(r"\d+", name):
        parts.append((0, int(token)))
    return (len(name), parts, name)


class Downloader(object):
    """Resumable downloader with curl/wget fallback and mirror support."""

    def __init__(self, console=None, log: Optional[Callable[..., None]] = None, offline: bool = False):
        self.console = console
        self.log = log or (lambda *a, **k: None)
        self.offline = offline

    # -- public API --------------------------------------------------------
    def download(
        self,
        urls: Sequence[str],
        dest,
        label: str = "download",
        sha256: Optional[str] = None,
        retries: int = 3,
        timeout: int = 600,
    ) -> Path:
        """Download the first URL that works into ``dest``.

        A partially downloaded ``.part`` file is resumed on the next attempt
        and on the next run, so large toolchains survive flaky connections.
        """
        if self.offline:
            raise NetError("Offline mode is on (--offline): refusing to download %s." % label)
        if isinstance(urls, str):
            urls = [urls]
        urls = [u for u in urls if u]
        if not urls:
            raise NetError("No download URL provided for %s." % label)

        dest = Path(dest)
        util.ensure_parent(dest)
        errors: List[str] = []
        for url in urls:
            for attempt in range(1, max(1, retries) + 1):
                try:
                    self._download_one(url, dest, label, timeout)
                    if sha256:
                        digest = util.sha256_file(dest)
                        if digest and digest.lower() != sha256.lower():
                            bad = dest.with_suffix(dest.suffix + ".bad")
                            try:
                                dest.replace(bad)
                            except OSError:
                                pass
                            raise NetError("Checksum mismatch for %s (expected %s, got %s)." % (label, sha256[:12], (digest or "?")[:12]))
                    return dest
                except NetError as exc:
                    errors.append(str(exc))
                    self.log("download_attempt_failed", url=url, attempt=attempt, error=str(exc))
                    if attempt < retries and self.console:
                        self.console.detail("retry %d/%d for %s" % (attempt, retries, label))
                    time.sleep(min(8, 1.5 * attempt))
        raise NetError("Could not download %s. Tried: %s" % (label, " | ".join(errors[-4:])))

    # -- transports --------------------------------------------------------
    def _download_one(self, url: str, dest: Path, label: str, timeout: int) -> None:
        part = dest.with_suffix(dest.suffix + ".part")
        try:
            self._urllib_download(url, part, label, timeout)
        except NetError as first_error:
            external = self._external_download(url, part, label, timeout)
            if not external:
                raise first_error
        dest.parent.mkdir(parents=True, exist_ok=True)
        if part.exists():
            if dest.exists():
                dest.unlink()
            part.replace(dest)
        elif not dest.exists():
            raise NetError("Download of %s produced no file." % label)

    def _urllib_download(self, url: str, part: Path, label: str, timeout: int) -> None:
        headers = {}
        offset = part.stat().st_size if part.exists() else 0
        if offset:
            headers["Range"] = "bytes=%d-" % offset
        try:
            response = _request(url, headers=headers, timeout=min(timeout, 120))
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and offset:  # already complete
                return
            raise NetError("HTTP %s for %s" % (exc.code, url))
        except Exception as exc:
            raise NetError("%s (%s)" % (exc, url))

        with response:
            if offset and response.status != 206:
                offset = 0  # server ignored the range: start over
            total_header = response.headers.get("Content-Length")
            try:
                total = int(total_header) if total_header else 0
            except ValueError:
                total = 0
            total = (total + offset) if total else 0
            mode = "ab" if offset else "wb"
            received = offset
            last_report = 0.0
            with open(str(part), mode) as handle:
                while True:
                    chunk = response.read(262144)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    now = time.time()
                    if self.console and (now - last_report) > 0.2 or received >= total:
                        self.console.progress(label, received, total)
                        last_report = now
            if total and received < total:
                raise NetError("Connection ended early (%s of %s) for %s" % (util.human_size(received), util.human_size(total), label))

    def _external_download(self, url: str, part: Path, label: str, timeout: int) -> bool:
        """Fall back to curl or wget when urllib cannot complete the transfer."""
        for tool, args in (
            ("curl", ["curl", "-fL", "--retry", "2", "--retry-delay", "2", "-C", "-", "--connect-timeout", "30", "-o", str(part), url]),
            ("wget", ["wget", "-q", "-c", "-T", "45", "-O", str(part), url]),
        ):
            binary = util.which(tool)
            if not binary:
                continue
            if self.console:
                self.console.detail("urllib failed, retrying with %s" % tool)
            result = util.run(args, timeout=timeout)
            if result.ok and part.exists() and part.stat().st_size > 0:
                return True
        return False
