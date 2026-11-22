"""Network lookups: modpack icon/link (Modrinth, CurseForge) and update check.

Everything here is best-effort: any failure just means "no icon/button/update".
Results are cached in cache.json so most launches make no request at all.
"""
import json
import re
import time
import urllib.request

from config import GITHUB_REPO, VERSION
from settings import cache_fresh, load_cache, save_cache

PACK_CACHE_SECONDS = 7 * 24 * 3600
UPDATE_CACHE_SECONDS = 24 * 3600


def get_json(url, timeout=5):
    req = urllib.request.Request(
        url, headers={"User-Agent": f"PrismRPC/{VERSION}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status != 200:  # e.g. 202 = "queued, try again later"
            raise OSError(f"HTTP {resp.status}")
        return json.loads(resp.read().decode("utf-8"))


# --- modpack icon and link -----------------------------------------------------
def managed_pack(inst_dir):
    """(source, project_id) for packs installed through Prism, else (None, None)."""
    try:
        text = (inst_dir / "instance.cfg").read_text(encoding="utf-8")
    except OSError:
        return None, None
    kind = re.search(r"^ManagedPackType=(\w+)", text, re.M | re.I)
    pid = re.search(r"^ManagedPackID=(\S+)", text, re.M)
    if not (kind and pid):
        return None, None
    kind = kind.group(1).lower()
    if kind == "modrinth":
        return "modrinth", pid.group(1)
    if kind in ("flame", "curseforge"):
        return "curseforge", pid.group(1)
    return None, None


def _modrinth(pid):
    p = get_json(f"https://api.modrinth.com/v2/project/{pid}")
    kind = p.get("project_type") or "project"
    slug = p.get("slug") or pid
    return {"icon": p.get("icon_url") or "", "url": f"https://modrinth.com/{kind}/{slug}"}


def _curseforge(pid):
    # CurseForge's own API needs a private key, so use CFWidget, a free public
    # mirror of project pages. The link works even if the lookup fails.
    fallback = f"https://www.curseforge.com/projects/{pid}"
    try:
        p = get_json(f"https://api.cfwidget.com/{pid}")
    except Exception:
        return {"icon": "", "url": fallback}
    urls = p.get("urls") if isinstance(p.get("urls"), dict) else {}
    return {"icon": p.get("thumbnail") or "",
            "url": urls.get("curseforge") or fallback}


def lookup_pack(inst_dir):
    """{'icon': url, 'url': url} for the instance's modpack, or {}."""
    source, pid = managed_pack(inst_dir)
    if not source:
        return {}
    key = f"pack:{source}:{pid}"
    cache = load_cache()
    hit = cache.get(key)
    if cache_fresh(hit, PACK_CACHE_SECONDS):
        return hit["data"]
    try:
        data = _modrinth(pid) if source == "modrinth" else _curseforge(pid)
    except Exception:
        return hit["data"] if isinstance(hit, dict) else {}  # stale beats nothing
    if data.get("icon") or data.get("url"):
        cache[key] = {"t": time.time(), "data": data}
        save_cache(cache)
    return data


# --- update check -----------------------------------------------------------------
def repo_slug(value):
    """'owner/repo' from a slug or a full GitHub URL, else ''.

    Accepts "owner/repo", "https://github.com/owner/repo", a trailing slash or
    ".git", and the SSH form "git@github.com:owner/repo.git".
    """
    text = (value or "").strip()
    if not text:
        return ""
    text = re.sub(r"^(?:https?://|git@)?(?:www\.)?github\.com[/:]", "", text, flags=re.I)
    text = text.strip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    parts = [p for p in text.split("/") if p]
    if len(parts) < 2 or not re.fullmatch(r"[\w.-]+/[\w.-]+", "/".join(parts[:2])):
        return ""
    return "/".join(parts[:2])


def repo_url(value=GITHUB_REPO):
    slug = repo_slug(value)
    return f"https://github.com/{slug}" if slug else ""


def _version_tuple(tag):
    """Numbers of the version, ignoring a pre-release/build suffix ("-beta2", "+3")."""
    return tuple(int(n) for n in re.findall(r"\d+", re.split(r"[-+]", str(tag))[0]))


def is_newer(tag, current=VERSION):
    """True when `tag` is a later version than `current`.

    Missing parts count as zero ("1.2" == "1.2.0"), and a fourth part is
    compared instead of being ignored, so "1.1.0.1" is newer than "1.1.0".
    """
    try:
        newer, base = _version_tuple(tag), _version_tuple(current)
    except ValueError:
        return False
    if not newer:
        return False
    width = max(len(newer), len(base))
    return newer + (0,) * (width - len(newer)) > base + (0,) * (width - len(base))


def check_for_update(force=False):
    """(tag, release_url) if a newer release exists, else None.

    Asks GitHub at most once a day unless force=True. Returns None when the
    repository isn't configured or GitHub can't be reached (a cached answer is
    still used when the request fails).
    """
    slug = repo_slug(GITHUB_REPO)
    if not slug:
        return None
    cache = load_cache()
    entry = cache.get("update")
    if not isinstance(entry, dict):
        entry = {}
    if force or not cache_fresh(entry, UPDATE_CACHE_SECONDS):
        try:
            rel = get_json(f"https://api.github.com/repos/{slug}/releases/latest")
        except Exception:
            rel = None  # offline: fall back to whatever we cached last time
        if rel is not None:
            entry = {"t": time.time(), "tag": rel.get("tag_name") or "",
                     "url": rel.get("html_url") or ""}
            cache["update"] = entry
            save_cache(cache)
    if entry.get("tag") and is_newer(entry["tag"]):
        return entry["tag"], entry.get("url") or f"https://github.com/{slug}/releases"
    return None


UPDATE_CHECK_ENABLED = bool(repo_slug(GITHUB_REPO))
