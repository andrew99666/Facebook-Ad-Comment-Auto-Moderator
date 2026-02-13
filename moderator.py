"""
Facebook Ad Comment Auto-Moderator v8.1 — Rate-Limit Aware by Andrew, Telegram handle @partofwhole

Commands:
    python moderator.py snapshot [POST_ID]
    python moderator.py monitor
    python moderator.py status
"""

import requests
import time
import signal
import sys
import logging
import json
import os
from datetime import datetime, timezone

# ================= CONFIGURATION =================
ACCESS_TOKEN = "EAAT79SNPYcUBQmf3aFZA2HuRFtQkppHriw3CWD8gD81TGhZCNsjb8IxPGknAy9EgUcasv7cLFkhceE3z0GNyV0PUHYRDl2A0EkrHHg9UYvo3JNjFFUeRKfRXoTLkfXXMJnpdnDbYNHhhk7KkcMgWrzZCpkEmmngCAR7hQ2XT7ODcuLz5gMZATGhLg6IS"
PAGE_ID = "921081844424797"
AD_ACCOUNT_ID = "1686994929351995"
API_VERSION = "v21.0"

DRY_RUN = False             # True for test run. False for real run. <<< CHANGE THIS <<<
REGISTRY_FILE = "known_comments.json"

# Rate limit management
PAGE_CALLS_PER_HOUR = 200   # Facebook's Page token limit
CALLS_BUFFER = 20           # Reserve for hides + safety margin
POSTS_CACHE_TTL = 1800      # Refresh post list every 30 min
MIN_INTERVAL = 120          # Never scan faster than 2 min
MAX_INTERVAL = 900          # Never slower than 15 min
# =================================================

_user_token = ACCESS_TOKEN
_page_token = None
_token_type = None

# Rate tracking
_call_timestamps = []       # Timestamps of Page-token API calls
_cached_posts = []
_posts_fetched_at = 0

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s — %(levelname)s — %(message)s',
    handlers=[
        logging.FileHandler('moderator.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def shutdown(sig, frame):
    logger.info("\n👋 Shutting down gracefully...")
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown)
if hasattr(signal, 'SIGTERM'):
    signal.signal(signal.SIGTERM, shutdown)


def now_utc():
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S+0000')


# ============== RATE LIMIT TRACKING ==============

def _track_page_call():
    """Record a Page-token API call for budget tracking."""
    now = time.time()
    _call_timestamps.append(now)
    # Prune older than 1 hour
    cutoff = now - 3600
    while _call_timestamps and _call_timestamps[0] < cutoff:
        _call_timestamps.pop(0)


def calls_used_this_hour():
    now = time.time()
    cutoff = now - 3600
    return sum(1 for t in _call_timestamps if t >= cutoff)


def calls_remaining():
    return max(0, PAGE_CALLS_PER_HOUR - calls_used_this_hour())


def calc_interval(num_posts):
    """
    Auto-calculate safe interval between scans.
    Each scan = num_posts calls (1 per post, single-pass).
    """
    if num_posts == 0:
        return MIN_INTERVAL
    budget = PAGE_CALLS_PER_HOUR - CALLS_BUFFER  # 180 usable
    max_cycles = budget / num_posts               # 180/29 ≈ 6.2
    interval = 3600 / max_cycles                  # 3600/6.2 ≈ 580s
    interval = max(MIN_INTERVAL, min(MAX_INTERVAL, int(interval)))
    return interval


# ============== API HELPERS ==============

def get_headers(use_page_token=True):
    if use_page_token and _page_token:
        return {'Authorization': f'Bearer {_page_token}'}
    return {'Authorization': f'Bearer {_user_token}'}


def safe_request(method, url, max_retries=3, use_page_token=True, **kwargs):
    if 'headers' not in kwargs:
        kwargs['headers'] = get_headers(use_page_token)
    for attempt in range(max_retries):
        try:
            if use_page_token:
                _track_page_call()

            resp = method(url, **kwargs)
            data = resp.json()
            error_code = data.get('error', {}).get('code')
            if error_code in (4, 17, 32):
                wait = 2 ** attempt * 30
                logger.warning(f"⏳ Rate limited (code {error_code}). "
                               f"Waiting {wait}s... "
                               f"[{calls_used_this_hour()} calls this hour]")
                time.sleep(wait)
                continue
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error (attempt {attempt+1}/"
                         f"{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
    return {'error': {'message': 'Max retries exceeded', 'type': 'RetryError'}}


def fetch_paginated(url, params, extract_fn, label="",
                    use_page_token=True):
    results = set()
    while True:
        resp = safe_request(requests.get, url, params=params,
                            use_page_token=use_page_token)
        if 'error' in resp:
            logger.warning(f"    ❌ {label}: "
                           f"{resp['error'].get('message', 'Unknown')}")
            return results
        for item in resp.get('data', []):
            value = extract_fn(item)
            if value:
                results.add(value)
        if 'paging' in resp and 'next' in resp['paging']:
            url = resp['paging']['next']
            params = {}
        else:
            break
    return results


# ============== TOKEN / ACCOUNT SETUP ==============

def check_token_type():
    global _token_type
    logger.info("  > Checking token type...")
    url = f"https://graph.facebook.com/{API_VERSION}/debug_token"
    params = {'input_token': ACCESS_TOKEN, 'access_token': ACCESS_TOKEN}
    res = safe_request(requests.get, url, params=params,
                       use_page_token=False)
    data = res.get('data', {})
    _token_type = data.get('type', 'UNKNOWN')
    scopes = data.get('scopes', [])
    logger.info(f"    Token type: {_token_type}")
    logger.info(f"    Scopes: {', '.join(scopes)}")
    if _token_type == 'PAGE':
        logger.error("  ╔══════════════════════════════════════╗")
        logger.error("  ║  TOKEN IS PAGE TYPE — NEED USER     ║")
        logger.error("  ╚══════════════════════════════════════╝")
        return False
    if _token_type == 'USER':
        logger.info("    ✅ USER token confirmed")
    return True


def verify_page():
    url = f"https://graph.facebook.com/{API_VERSION}/{PAGE_ID}"
    res = safe_request(requests.get, url, params={'fields': 'name'},
                       use_page_token=False)
    if 'name' in res:
        logger.info(f"  ✅ Page: {res['name']}")
        return True
    logger.error("  ❌ Cannot access page")
    return False


def upgrade_to_page_token():
    global _page_token
    logger.info("  > Exchanging for Page Access Token...")
    url = f"https://graph.facebook.com/{API_VERSION}/{PAGE_ID}"
    res = safe_request(requests.get, url,
                       params={'fields': 'access_token'},
                       use_page_token=False)
    if 'access_token' in res:
        _page_token = res['access_token']
        logger.info("  ✅ Got Page Token")
        return True
    _page_token = _user_token
    return False


def diagnose_ad_account():
    global AD_ACCOUNT_ID
    logger.info(f"  > Verifying Ad Account: act_{AD_ACCOUNT_ID}...")
    url = f"https://graph.facebook.com/{API_VERSION}/act_{AD_ACCOUNT_ID}"
    res = safe_request(requests.get, url,
                       params={'fields': 'name,account_status'},
                       use_page_token=False)
    if 'name' in res:
        logger.info(f"    ✅ {res['name']}")
        return True
    logger.warning("    ❌ Not accessible, searching...")
    url = f"https://graph.facebook.com/{API_VERSION}/me/adaccounts"
    res = safe_request(requests.get, url,
                       params={'fields': 'name,account_id,account_status',
                               'limit': 25},
                       use_page_token=False)
    if 'data' in res:
        for acc in res['data']:
            if acc.get('account_status') == 1:
                AD_ACCOUNT_ID = acc['account_id']
                logger.info(f"    🔄 Auto-selected: act_{AD_ACCOUNT_ID}")
                return True
    return False


# ============== POST DISCOVERY ==============

def get_active_ad_posts():
    """Fetch from API (not cached). Uses User token = Marketing API limits."""
    if _token_type == 'USER':
        logger.info(f"  > Ads API (act_{AD_ACCOUNT_ID})...")
        ids = fetch_paginated(
            url=f"https://graph.facebook.com/{API_VERSION}/"
                f"act_{AD_ACCOUNT_ID}/ads",
            params={
                'fields': 'creative{effective_object_story_id}',
                'effective_status': '["ACTIVE","PAUSED"]',
                'limit': 200,
            },
            extract_fn=lambda ad: ad.get('creative', {}).get(
                'effective_object_story_id'),
            label="Ads API",
            use_page_token=False
        )
        if ids:
            logger.info(f"    ✅ {len(ids)} unique ad posts")
            return list(ids)

    logger.info("  > Fallback: /ads_posts...")
    ids = fetch_paginated(
        url=f"https://graph.facebook.com/{API_VERSION}/"
            f"{PAGE_ID}/ads_posts",
        params={'limit': 100, 'fields': 'id'},
        extract_fn=lambda p: p.get('id'),
        label="ads_posts", use_page_token=True
    )
    if ids:
        return list(ids)

    logger.info("  > Fallback: /published_posts...")
    ids = fetch_paginated(
        url=f"https://graph.facebook.com/{API_VERSION}/"
            f"{PAGE_ID}/published_posts",
        params={'limit': 100, 'fields': 'id'},
        extract_fn=lambda p: p.get('id'),
        label="published_posts", use_page_token=True
    )
    return list(ids)


def get_active_posts_cached():
    """Return cached post list. Refresh after POSTS_CACHE_TTL."""
    global _cached_posts, _posts_fetched_at
    age = time.time() - _posts_fetched_at
    if _cached_posts and age < POSTS_CACHE_TTL:
        logger.info(f"  > Post list cached ({len(_cached_posts)} posts,"
                    f" refreshes in {int(POSTS_CACHE_TTL - age)}s)")
        return _cached_posts
    _cached_posts = get_active_ad_posts()
    _posts_fetched_at = time.time()
    return _cached_posts


# ============== COMMENT FETCHING ==============

COMMENT_FIELDS = 'id,created_time,is_hidden,message,from,parent'


def _fetch_comments_pass(post_id, comments_by_id,
                         filter_type=None, limit=500,
                         include_hidden=False):
    """Single fetch pass. Used by both snapshot (multi) and monitor (single)."""
    url = f"https://graph.facebook.com/{API_VERSION}/{post_id}/comments"
    params = {
        'limit': limit,
        'fields': COMMENT_FIELDS,
        'summary': 'true',
    }
    if filter_type:
        params['filter'] = filter_type
    if include_hidden:
        params['include_hidden'] = 'true'

    fb_total = 0
    new_found = 0

    while True:
        res = safe_request(requests.get, url, params=params,
                           use_page_token=True)
        if 'error' in res:
            break
        if 'summary' in res and fb_total == 0:
            fb_total = res['summary'].get('total_count', 0)
        for comment in res.get('data', []):
            cid = comment.get('id')
            if cid and cid not in comments_by_id:
                comments_by_id[cid] = comment
                new_found += 1
        if 'paging' in res and 'next' in res['paging']:
            url = res['paging']['next']
            params = {}
        else:
            break
    return fb_total, new_found


def fetch_all_comments(post_id):
    """
    MULTI-PASS: Try 4 strategies to maximize comment recovery.
    Used by SNAPSHOT only. Costs 1-4 API calls per post.
    """
    comments_by_id = {}
    fb_total, _ = _fetch_comments_pass(
        post_id, comments_by_id, filter_type='stream')

    if len(comments_by_id) >= fb_total:
        return list(comments_by_id.values()), fb_total

    _, found = _fetch_comments_pass(
        post_id, comments_by_id, filter_type='stream',
        include_hidden=True)
    if found > 0:
        logger.info(f"    🔄 Pass 2 (include_hidden): +{found}")

    if len(comments_by_id) >= fb_total:
        return list(comments_by_id.values()), fb_total

    _, found = _fetch_comments_pass(
        post_id, comments_by_id, filter_type=None)
    if found > 0:
        logger.info(f"    🔄 Pass 3 (no filter): +{found}")

    if len(comments_by_id) >= fb_total:
        return list(comments_by_id.values()), fb_total

    _, found = _fetch_comments_pass(
        post_id, comments_by_id, filter_type='stream', limit=25)
    if found > 0:
        logger.info(f"    🔄 Pass 4 (small batches): +{found}")

    return list(comments_by_id.values()), fb_total


def fetch_comments_quick(post_id):
    """
    SINGLE-PASS: One API call per post.
    Used by MONITOR. New organic comments are always visible in pass 1.
    Costs exactly 1 API call (29 posts = 29 calls/cycle).
    """
    comments_by_id = {}
    _fetch_comments_pass(post_id, comments_by_id,
                         filter_type='stream', include_hidden=True)
    return list(comments_by_id.values())


# ============== REGISTRY ==============

def load_registry():
    if not os.path.exists(REGISTRY_FILE):
        return {}
    try:
        with open(REGISTRY_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"  ❌ Registry corrupt: {e}")
        backup = f"{REGISTRY_FILE}.bak.{int(time.time())}"
        os.rename(REGISTRY_FILE, backup)
        logger.info(f"  Backed up to {backup}, starting fresh")
        return {}


def save_registry(registry):
    tmp = REGISTRY_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(registry, f, indent=2)
    os.replace(tmp, REGISTRY_FILE)


# ============== SNAPSHOT ==============

def snapshot_post(post_id):
    """
    MULTI-PASS fetch → register all visible comment IDs.
    Merges with existing registry (never removes IDs).
    """
    all_comments, fb_total = fetch_all_comments(post_id)

    registry = load_registry()
    existing_entry = registry.get(post_id, {})
    existing_ids = set(existing_entry.get('comment_ids', []))
    old_count = len(existing_ids)

    for c in all_comments:
        existing_ids.add(c['id'])

    new_count = len(existing_ids) - old_count

    page_id_str = str(PAGE_ID)
    user_top = page_count = reply_count = 0
    for c in all_comments:
        author_id = c.get('from', {}).get('id')
        is_page = author_id and str(author_id) == page_id_str
        if is_page:
            page_count += 1
        elif 'parent' in c:
            reply_count += 1
        else:
            user_top += 1

    registry[post_id] = {
        'first_snapshot': existing_entry.get('first_snapshot', now_utc()),
        'last_snapshot': now_utc(),
        'comment_ids': list(existing_ids),
        'fb_total': fb_total,
        'api_visible': len(all_comments),
    }
    save_registry(registry)

    logger.info(f"  📸 {post_id}")
    logger.info(f"     Visible: {len(all_comments)} | FB total: {fb_total}")
    logger.info(f"     User: {user_top} | Page: {page_count}"
                f" | Replies: {reply_count}")
    logger.info(f"     Registry: {len(existing_ids)} IDs"
                f" (+{new_count} new)")

    if fb_total > len(all_comments):
        gap = fb_total - len(all_comments)
        logger.warning(f"     ⚠️ {gap} invisible to API")

    return len(existing_ids)


# ============== MONITOR ==============

def monitor_post(post_id, registry_entry):
    """
    SINGLE-PASS fetch → hide anything not in registry.
    1 API call per post (+ 1 per hide).
    """
    known_ids = set(registry_entry.get('comment_ids', []))
    first_snapshot = registry_entry.get('first_snapshot', '')
    page_id_str = str(PAGE_ID)

    all_comments = fetch_comments_quick(post_id)
    if not all_comments:
        return

    known = 0
    page = 0
    already_hidden = 0
    auto_registered = []
    to_hide = []

    for comment in all_comments:
        cid = comment['id']
        author_id = comment.get('from', {}).get('id')
        is_page = author_id and str(author_id) == page_id_str

        if is_page:
            page += 1
            continue

        if cid in known_ids:
            known += 1
            continue

        if comment.get('is_hidden'):
            already_hidden += 1
            continue

        # Safety: pre-snapshot comment = probably seeded but was invisible
        created = comment.get('created_time', '')
        if first_snapshot and created and created <= first_snapshot:
            auto_registered.append(comment)
            continue

        to_hide.append(comment)

    # Summary
    new_count = len(to_hide)
    icon = "🔴" if new_count > 0 else "✅"
    line = (f"  {icon} {post_id}: "
            f"known={known} page={page}")
    if already_hidden:
        line += f" hidden={already_hidden}"
    if auto_registered:
        line += f" auto-reg={len(auto_registered)}"
    if new_count:
        line += f" → NEW={new_count}"
    logger.info(line)

    # Auto-register pre-snapshot discoveries
    if auto_registered:
        registry = load_registry()
        entry = registry.get(post_id, {})
        ids = set(entry.get('comment_ids', []))
        for c in auto_registered:
            ids.add(c['id'])
            msg = c.get('message', '').replace('\n', ' ')[:40]
            logger.info(f"    🔍 Auto-registered: \"{msg}\"")
        entry['comment_ids'] = list(ids)
        registry[post_id] = entry
        save_registry(registry)

    if not to_hide:
        return

    # Hide
    hidden_ok = 0
    hidden_fail = 0

    for comment in to_hide:
        # Budget check before each hide
        if calls_remaining() < 5:
            logger.warning("    ⚠️ API budget critical — pausing hides")
            break

        msg = comment.get('message', '').replace('\n', ' ')[:50]
        ctype = "reply" if 'parent' in comment else "comment"

        if DRY_RUN:
            logger.info(f"    [DRY] Would hide {ctype}: \"{msg}\"")
        else:
            logger.info(f"    🚫 Hiding {ctype}: \"{msg}\"")
            resp = safe_request(
                requests.post,
                f"https://graph.facebook.com/{API_VERSION}/"
                f"{comment['id']}",
                params={'is_hidden': 'true'},
                use_page_token=True
            )
            if resp.get('success') is True:
                hidden_ok += 1
            else:
                hidden_fail += 1
                logger.error(f"      ⚠️ FAILED: {resp}")
            time.sleep(0.5)

    if DRY_RUN and to_hide:
        logger.info(f"    📊 DRY RUN: {len(to_hide)} would be hidden")
    elif hidden_ok or hidden_fail:
        logger.info(f"    📊 Hidden: {hidden_ok} | Failed: {hidden_fail}")


# ============== COMMANDS ==============

def cmd_snapshot(specific_post=None):
    if specific_post:
        logger.info(f"\n📸 Snapshotting: {specific_post}")
        snapshot_post(specific_post)
    else:
        logger.info("\n📸 Snapshotting ALL ad posts...")
        posts = get_active_ad_posts()
        logger.info(f"  Found {len(posts)} posts\n")
        total_ids = 0
        for i, post_id in enumerate(posts):
            logger.info(f"  [{i+1}/{len(posts)}]")
            total_ids += snapshot_post(post_id)
            time.sleep(1)
        logger.info(f"\n{'='*55}")
        logger.info(f"  ✅ Snapshot complete!")
        logger.info(f"  {len(posts)} posts, {total_ids} total comment IDs")
        logger.info(f"  Next: python moderator.py monitor")


def cmd_monitor():
    if DRY_RUN:
        logger.info("\n⚠️  DRY RUN MODE")
    else:
        logger.info("\n🚨 LIVE MODE")

    while True:
        start = datetime.now()

        registry = load_registry()
        if not registry:
            logger.error("  ❌ Registry empty!"
                         " Run: python moderator.py snapshot")
            time.sleep(60)
            continue

        try:
            active_posts = get_active_posts_cached()
        except Exception as e:
            logger.error(f"  ❌ Post fetch failed: {e}")
            time.sleep(60)
            continue

        # Calculate safe interval BEFORE scanning
        interval = calc_interval(len(active_posts))
        budget = calls_remaining()

        logger.info(f"\n{'='*55}")
        logger.info(
            f"[{start.strftime('%H:%M:%S')}] Scan "
            f"| {len(active_posts)} posts "
            f"| budget: {budget}/{PAGE_CALLS_PER_HOUR} "
            f"| interval: {interval}s")

        if budget < len(active_posts) + 5:
            wait = 300
            logger.warning(f"  ⚠️ Budget too low for full scan."
                           f" Waiting {wait}s...")
            time.sleep(wait)
            continue

        unregistered = []
        for post_id in active_posts:
            if post_id in registry:
                try:
                    monitor_post(post_id, registry[post_id])
                except Exception as e:
                    logger.error(f"  ❌ Error on {post_id}: {e}")
            else:
                unregistered.append(post_id)

        if unregistered:
            logger.warning(
                f"\n  ⚠️ {len(unregistered)} post(s) NOT registered:")
            for pid in unregistered[:5]:
                logger.warning(f"    • {pid}")
            if len(unregistered) > 5:
                logger.warning(f"    ...and {len(unregistered)-5} more")

        elapsed = (datetime.now() - start).seconds
        remaining = calls_remaining()
        logger.info(
            f"\n  Done ({elapsed}s) | Budget: {remaining}/"
            f"{PAGE_CALLS_PER_HOUR} | Next in {interval}s"
            f" ({interval//60}m {interval%60}s)")
        time.sleep(interval)


def cmd_status():
    registry = load_registry()
    if not registry:
        logger.info("  Registry empty."
                     " Run: python moderator.py snapshot")
        return

    logger.info(f"\n📋 Registry: {REGISTRY_FILE}")
    logger.info(f"   {len(registry)} posts")
    logger.info(f"\n   {'Post ID':<40} {'IDs':>5}  {'API':>4}"
                f"  {'FB':>4}  Snapshot")
    logger.info(f"   {'─'*75}")

    total = 0
    for pid, d in sorted(registry.items()):
        count = len(d.get('comment_ids', []))
        total += count
        vis = d.get('api_visible', '?')
        fb = d.get('fb_total', '?')
        snap = d.get('last_snapshot', '?')[:19]
        logger.info(f"   {pid:<40} {count:>5}  {vis:>4}"
                     f"  {fb:>4}  {snap}")

    logger.info(f"   {'─'*75}")
    logger.info(f"   Total: {total} registered IDs")

    interval = calc_interval(len(registry))
    logger.info(f"\n   Monitor interval: {interval}s"
                f" ({interval//60}m {interval%60}s)")
    logger.info(f"   Calls/cycle: ~{len(registry)}"
                f" | Cycles/hour: ~{3600//interval}"
                f" | Calls/hour: ~{len(registry) * (3600//interval)}"
                f" / {PAGE_CALLS_PER_HOUR}")


# ============== MAIN ==============

def setup():
    logger.info("=" * 55)
    logger.info("  Facebook Ad Auto-Moderator v8.1 (Rate-Aware)")
    logger.info("=" * 55)
    if not check_token_type():
        sys.exit(1)
    if not verify_page():
        sys.exit(1)
    upgrade_to_page_token()
    diagnose_ad_account()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'monitor'
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    if cmd == 'status':
        cmd_status()
    else:
        setup()
        if cmd == 'snapshot':
            cmd_snapshot(specific_post=arg)
        elif cmd == 'monitor':
            cmd_monitor()
        else:
            logger.info(f"\nUnknown: {cmd}")
            logger.info("  snapshot [POST_ID]")
            logger.info("  monitor")
            logger.info("  status")

# ================= CONFIGURATION =================
ACCESS_TOKEN = "EAAT79SNPYcUBQmf3aFZA2HuRFtQkppHriw3CWD8gD81TGhZCNsjb8IxPGknAy9EgUcasv7cLFkhceE3z0GNyV0PUHYRDl2A0EkrHHg9UYvo3JNjFFUeRKfRXoTLkfXXMJnpdnDbYNHhhk7KkcMgWrzZCpkEmmngCAR7hQ2XT7ODcuLz5gMZATGhLg6IS"
PAGE_ID = "921081844424797"
AD_ACCOUNT_ID = "1686994929351995"
API_VERSION = "v21.0"

CHECK_INTERVAL = 300
DRY_RUN = True
REGISTRY_FILE = "known_comments.json"
# =================================================

_user_token = ACCESS_TOKEN
_page_token = None
_token_type = None

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s — %(levelname)s — %(message)s',
    handlers=[
        logging.FileHandler('moderator.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def shutdown(sig, frame):
    logger.info("\n👋 Shutting down gracefully...")
    sys.exit(0)

signal.signal(signal.SIGINT, shutdown)
if hasattr(signal, 'SIGTERM'):
    signal.signal(signal.SIGTERM, shutdown)


def now_utc():
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S+0000')


# ============== API HELPERS ==============

def get_headers(use_page_token=True):
    if use_page_token and _page_token:
        return {'Authorization': f'Bearer {_page_token}'}
    return {'Authorization': f'Bearer {_user_token}'}


def safe_request(method, url, max_retries=3, use_page_token=True, **kwargs):
    if 'headers' not in kwargs:
        kwargs['headers'] = get_headers(use_page_token)
    for attempt in range(max_retries):
        try:
            resp = method(url, **kwargs)
            data = resp.json()
            error_code = data.get('error', {}).get('code')
            if error_code in (4, 17, 32):
                wait = 2 ** attempt * 30
                logger.warning(f"⏳ Rate limited. Waiting {wait}s...")
                time.sleep(wait)
                continue
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
    return {'error': {'message': 'Max retries exceeded', 'type': 'RetryError'}}


def fetch_paginated(url, params, extract_fn, label="", use_page_token=True):
    results = set()
    while True:
        resp = safe_request(requests.get, url, params=params, use_page_token=use_page_token)
        if 'error' in resp:
            logger.warning(f"    ❌ {label}: {resp['error'].get('message', 'Unknown')}")
            return results
        for item in resp.get('data', []):
            value = extract_fn(item)
            if value:
                results.add(value)
        if 'paging' in resp and 'next' in resp['paging']:
            url = resp['paging']['next']
            params = {}
        else:
            break
    return results


# ============== TOKEN / ACCOUNT SETUP ==============

def check_token_type():
    global _token_type
    logger.info("  > Checking token type...")
    url = f"https://graph.facebook.com/{API_VERSION}/debug_token"
    params = {'input_token': ACCESS_TOKEN, 'access_token': ACCESS_TOKEN}
    res = safe_request(requests.get, url, params=params, use_page_token=False)
    data = res.get('data', {})
    _token_type = data.get('type', 'UNKNOWN')
    scopes = data.get('scopes', [])
    logger.info(f"    Token type: {_token_type}")
    logger.info(f"    Scopes: {', '.join(scopes)}")
    if _token_type == 'PAGE':
        logger.error("  ╔══════════════════════════════════════════════╗")
        logger.error("  ║  TOKEN IS PAGE TYPE — NEED USER TYPE        ║")
        logger.error("  ╚══════════════════════════════════════════════╝")
        return False
    if _token_type == 'USER':
        logger.info("    ✅ USER token confirmed")
    return True


def verify_page():
    url = f"https://graph.facebook.com/{API_VERSION}/{PAGE_ID}"
    res = safe_request(requests.get, url, params={'fields': 'name'}, use_page_token=False)
    if 'name' in res:
        logger.info(f"  ✅ Page: {res['name']}")
        return True
    logger.error("  ❌ Cannot access page")
    return False


def upgrade_to_page_token():
    global _page_token
    logger.info("  > Exchanging for Page Access Token...")
    url = f"https://graph.facebook.com/{API_VERSION}/{PAGE_ID}"
    res = safe_request(requests.get, url, params={'fields': 'access_token'},
                       use_page_token=False)
    if 'access_token' in res:
        _page_token = res['access_token']
        logger.info("  ✅ Got Page Token")
        return True
    _page_token = _user_token
    return False


def diagnose_ad_account():
    global AD_ACCOUNT_ID
    logger.info(f"  > Verifying Ad Account: act_{AD_ACCOUNT_ID}...")
    url = f"https://graph.facebook.com/{API_VERSION}/act_{AD_ACCOUNT_ID}"
    res = safe_request(requests.get, url,
                       params={'fields': 'name,account_status'},
                       use_page_token=False)
    if 'name' in res:
        logger.info(f"    ✅ {res['name']}")
        return True
    logger.warning("    ❌ Not accessible, searching...")
    url = f"https://graph.facebook.com/{API_VERSION}/me/adaccounts"
    res = safe_request(requests.get, url,
                       params={'fields': 'name,account_id,account_status', 'limit': 25},
                       use_page_token=False)
    if 'data' in res:
        for acc in res['data']:
            if acc.get('account_status') == 1:
                AD_ACCOUNT_ID = acc['account_id']
                logger.info(f"    🔄 Auto-selected: act_{AD_ACCOUNT_ID}")
                return True
    return False


# ============== POST DISCOVERY ==============

def get_active_ad_posts():
    if _token_type == 'USER':
        logger.info(f"  > [1/3] Ads API (act_{AD_ACCOUNT_ID})...")
        ids = fetch_paginated(
            url=f"https://graph.facebook.com/{API_VERSION}/act_{AD_ACCOUNT_ID}/ads",
            params={
                'fields': 'creative{effective_object_story_id}',
                'effective_status': '["ACTIVE","PAUSED"]',
                'limit': 200,
            },
            extract_fn=lambda ad: ad.get('creative', {}).get(
                'effective_object_story_id'),
            label="Ads API",
            use_page_token=False
        )
        if ids:
            logger.info(f"    ✅ Found {len(ids)} unique ad posts")
            return list(ids)

    logger.info("  > [2/3] /ads_posts...")
    ids = fetch_paginated(
        url=f"https://graph.facebook.com/{API_VERSION}/{PAGE_ID}/ads_posts",
        params={'limit': 100, 'fields': 'id'},
        extract_fn=lambda p: p.get('id'),
        label="ads_posts", use_page_token=True
    )
    if ids:
        return list(ids)

    logger.info("  > [3/3] /published_posts...")
    ids = fetch_paginated(
        url=f"https://graph.facebook.com/{API_VERSION}/{PAGE_ID}/published_posts",
        params={'limit': 100, 'fields': 'id'},
        extract_fn=lambda p: p.get('id'),
        label="published_posts", use_page_token=True
    )
    return list(ids)


# ============== COMMENT FETCHING ==============

COMMENT_FIELDS = 'id,created_time,is_hidden,message,from,parent'


def _fetch_comments_pass(post_id, comments_by_id,
                         filter_type=None, limit=500, include_hidden=False):
    url = f"https://graph.facebook.com/{API_VERSION}/{post_id}/comments"
    params = {
        'limit': limit,
        'fields': COMMENT_FIELDS,
        'summary': 'true',
    }
    if filter_type:
        params['filter'] = filter_type
    if include_hidden:
        params['include_hidden'] = 'true'

    fb_total = 0
    new_found = 0

    while True:
        res = safe_request(requests.get, url, params=params, use_page_token=True)
        if 'error' in res:
            break
        if 'summary' in res and fb_total == 0:
            fb_total = res['summary'].get('total_count', 0)
        for comment in res.get('data', []):
            cid = comment.get('id')
            if cid and cid not in comments_by_id:
                comments_by_id[cid] = comment
                new_found += 1
        if 'paging' in res and 'next' in res['paging']:
            url = res['paging']['next']
            params = {}
        else:
            break
    return fb_total, new_found


def fetch_all_comments(post_id):
    comments_by_id = {}
    fb_total, _ = _fetch_comments_pass(
        post_id, comments_by_id, filter_type='stream')

    if len(comments_by_id) >= fb_total:
        return list(comments_by_id.values()), fb_total

    _, found = _fetch_comments_pass(
        post_id, comments_by_id, filter_type='stream', include_hidden=True)
    if found > 0:
        logger.info(f"    🔄 Pass 2 (include_hidden): +{found}")

    if len(comments_by_id) >= fb_total:
        return list(comments_by_id.values()), fb_total

    _, found = _fetch_comments_pass(
        post_id, comments_by_id, filter_type=None)
    if found > 0:
        logger.info(f"    🔄 Pass 3 (no filter): +{found}")

    if len(comments_by_id) >= fb_total:
        return list(comments_by_id.values()), fb_total

    _, found = _fetch_comments_pass(
        post_id, comments_by_id, filter_type='stream', limit=25)
    if found > 0:
        logger.info(f"    🔄 Pass 4 (small batches): +{found}")

    return list(comments_by_id.values()), fb_total


# ============== REGISTRY ==============

def load_registry():
    if not os.path.exists(REGISTRY_FILE):
        return {}
    try:
        with open(REGISTRY_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"  ❌ Registry corrupt: {e}")
        backup = f"{REGISTRY_FILE}.bak.{int(time.time())}"
        os.rename(REGISTRY_FILE, backup)
        logger.info(f"  Backed up to {backup}, starting fresh")
        return {}


def save_registry(registry):
    tmp = REGISTRY_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(registry, f, indent=2)
    os.replace(tmp, REGISTRY_FILE)


# ============== SNAPSHOT ==============

def snapshot_post(post_id):
    """
    Fetch all visible comments and register their IDs.
    MERGES with existing — safe to run multiple times.
    """
    all_comments, fb_total = fetch_all_comments(post_id)

    registry = load_registry()
    existing_entry = registry.get(post_id, {})
    existing_ids = set(existing_entry.get('comment_ids', []))
    old_count = len(existing_ids)

    # Add every visible comment ID
    for c in all_comments:
        existing_ids.add(c['id'])

    new_count = len(existing_ids) - old_count

    # Categorize for logging
    page_id_str = str(PAGE_ID)
    user_top = 0
    page_count = 0
    reply_count = 0
    for c in all_comments:
        author_id = c.get('from', {}).get('id')
        is_page = author_id and str(author_id) == page_id_str
        if is_page:
            page_count += 1
        elif 'parent' in c:
            reply_count += 1
        else:
            user_top += 1

    registry[post_id] = {
        'first_snapshot': existing_entry.get('first_snapshot', now_utc()),
        'last_snapshot': now_utc(),
        'comment_ids': list(existing_ids),
        'fb_total': fb_total,
        'api_visible': len(all_comments),
    }
    save_registry(registry)

    logger.info(f"  📸 {post_id}")
    logger.info(f"     Visible: {len(all_comments)} | FB total: {fb_total}")
    logger.info(f"     User top-level: {user_top} | Page: {page_count}"
                f" | Replies: {reply_count}")
    logger.info(f"     Registry: {len(existing_ids)} IDs"
                f" (+{new_count} new, {old_count} existing)")

    if fb_total > len(all_comments):
        gap = fb_total - len(all_comments)
        logger.warning(f"     ⚠️ {gap} invisible to API"
                       f" — run snapshot again later to catch more")

    return len(existing_ids)


# ============== MONITOR ==============

def monitor_post(post_id, registry_entry):
    """
    Fetch comments. Hide anything that is:
      - NOT from the page
      - NOT in the registry
      - NOT created before our first snapshot (safety net for late-appearing seeds)
      - NOT already hidden
    """
    known_ids = set(registry_entry.get('comment_ids', []))
    first_snapshot = registry_entry.get('first_snapshot', '')
    page_id_str = str(PAGE_ID)

    all_comments, fb_total = fetch_all_comments(post_id)
    if not all_comments:
        return

    known = 0
    page = 0
    already_hidden = 0
    auto_registered = []
    to_hide = []

    for comment in all_comments:
        cid = comment['id']
        author_id = comment.get('from', {}).get('id')
        is_page = author_id and str(author_id) == page_id_str

        # 1) Page's own comment — always safe
        if is_page:
            page += 1
            continue

        # 2) Already in registry — safe
        if cid in known_ids:
            known += 1
            continue

        # 3) Already hidden — just count it
        if comment.get('is_hidden'):
            already_hidden += 1
            continue

        # 4) Safety net: created BEFORE first snapshot?
        #    Probably a seeded comment that was invisible at snapshot time.
        #    Auto-register it instead of hiding.
        created = comment.get('created_time', '')
        if first_snapshot and created and created <= first_snapshot:
            auto_registered.append(comment)
            continue

        # 5) New organic comment — hide it
        to_hide.append(comment)

    # --- Log summary ---
    new_count = len(to_hide)
    icon = "🔴" if new_count > 0 else "✅"
    line = (f"  {icon} {post_id}: "
            f"visible={len(all_comments)} known={known} page={page}")
    if already_hidden:
        line += f" hidden={already_hidden}"
    if auto_registered:
        line += f" auto-reg={len(auto_registered)}"
    if new_count:
        line += f" → NEW={new_count}"
    logger.info(line)

    # --- Auto-register pre-snapshot comments ---
    if auto_registered:
        registry = load_registry()
        entry = registry.get(post_id, {})
        ids = set(entry.get('comment_ids', []))
        for c in auto_registered:
            ids.add(c['id'])
            msg = c.get('message', '').replace('\n', ' ')[:40]
            logger.info(f"    🔍 Auto-registered (pre-snapshot): \"{msg}\"")
        entry['comment_ids'] = list(ids)
        registry[post_id] = entry
        save_registry(registry)

    # --- Hide new comments ---
    if not to_hide:
        return

    hidden_ok = 0
    hidden_fail = 0

    for comment in to_hide:
        msg = comment.get('message', '').replace('\n', ' ')[:50]
        is_reply = 'parent' in comment
        ctype = "reply" if is_reply else "comment"

        if DRY_RUN:
            logger.info(f"    [DRY RUN] Would hide {ctype}: \"{msg}\"")
        else:
            logger.info(f"    🚫 Hiding {ctype}: \"{msg}\"")
            resp = safe_request(
                requests.post,
                f"https://graph.facebook.com/{API_VERSION}/{comment['id']}",
                params={'is_hidden': 'true'},
                use_page_token=True
            )
            if resp.get('success') is True:
                hidden_ok += 1
            else:
                hidden_fail += 1
                logger.error(f"      ⚠️ FAILED: {resp}")
            time.sleep(0.5)

    if DRY_RUN:
        logger.info(f"    📊 DRY RUN: {len(to_hide)} would be hidden")
    elif hidden_ok or hidden_fail:
        logger.info(f"    📊 Hidden: {hidden_ok} | Failed: {hidden_fail}")


# ============== COMMANDS ==============

def cmd_snapshot(specific_post=None):
    if specific_post:
        logger.info(f"\n📸 Snapshotting: {specific_post}")
        snapshot_post(specific_post)
    else:
        logger.info("\n📸 Snapshotting ALL ad posts...")
        posts = get_active_ad_posts()
        logger.info(f"  Found {len(posts)} posts\n")
        total_ids = 0
        for i, post_id in enumerate(posts):
            logger.info(f"  [{i+1}/{len(posts)}]")
            total_ids += snapshot_post(post_id)
            time.sleep(1)

        logger.info(f"\n{'='*55}")
        logger.info(f"  ✅ Snapshot complete!")
        logger.info(f"  {len(posts)} posts, {total_ids} total comment IDs registered")
        logger.info(f"  Registry: {REGISTRY_FILE}")
        logger.info(f"  Next: python moderator.py monitor")


def cmd_monitor():
    if DRY_RUN:
        logger.info("\n⚠️  DRY RUN MODE — nothing will be hidden")
    else:
        logger.info("\n🚨 LIVE MODE — new comments WILL be hidden")
    logger.info(f"  Interval: {CHECK_INTERVAL}s ({CHECK_INTERVAL/60:.0f}m)\n")

    while True:
        start = datetime.now()
        logger.info(f"\n{'='*55}")
        logger.info(f"[{start.strftime('%H:%M:%S')}] Scan")

        registry = load_registry()
        if not registry:
            logger.error("  ❌ Registry empty! Run: python moderator.py snapshot")
            time.sleep(CHECK_INTERVAL)
            continue

        try:
            active_posts = get_active_ad_posts()
        except Exception as e:
            logger.error(f"  ❌ Failed to get posts: {e}")
            time.sleep(60)
            continue

        unregistered = []
        for post_id in active_posts:
            if post_id in registry:
                try:
                    monitor_post(post_id, registry[post_id])
                except Exception as e:
                    logger.error(f"  ❌ Error on {post_id}: {e}")
            else:
                unregistered.append(post_id)

        if unregistered:
            logger.warning(
                f"\n  ⚠️ {len(unregistered)} post(s) NOT in registry:")
            for pid in unregistered[:5]:
                logger.warning(f"    • {pid}")
            if len(unregistered) > 5:
                logger.warning(f"    ...and {len(unregistered)-5} more")
            logger.warning("    Run: python moderator.py snapshot")

        elapsed = (datetime.now() - start).seconds
        logger.info(
            f"\n  Done ({elapsed}s). "
            f"Next in {CHECK_INTERVAL//60}m {CHECK_INTERVAL%60}s")
        time.sleep(CHECK_INTERVAL)


def cmd_status():
    registry = load_registry()
    if not registry:
        logger.info("  Registry empty. Run: python moderator.py snapshot")
        return

    logger.info(f"\n📋 Registry: {REGISTRY_FILE}")
    logger.info(f"   {len(registry)} posts\n")
    logger.info(f"   {'Post ID':<40} {'IDs':>5}  {'Visible':>8}  "
                f"{'FB Total':>9}  Snapshot")
    logger.info(f"   {'─'*90}")

    total = 0
    for pid, d in sorted(registry.items()):
        count = len(d.get('comment_ids', []))
        total += count
        vis = d.get('api_visible', '?')
        fb = d.get('fb_total', '?')
        snap = d.get('last_snapshot', '?')[:19]
        logger.info(f"   {pid:<40} {count:>5}  {vis:>8}  {fb:>9}  {snap}")

    logger.info(f"   {'─'*90}")
    logger.info(f"   Total: {total} registered IDs\n")


# ============== MAIN ==============

def setup():
    logger.info("=" * 55)
    logger.info("  Facebook Ad Auto-Moderator v8 (Registry-Based)")
    logger.info("=" * 55)
    if not check_token_type():
        sys.exit(1)
    if not verify_page():
        sys.exit(1)
    upgrade_to_page_token()
    diagnose_ad_account()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'monitor'
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    if cmd == 'status':
        # Status doesn't need API validation
        logging.basicConfig(level=logging.INFO)
        cmd_status()
    else:
        setup()
        if cmd == 'snapshot':
            cmd_snapshot(specific_post=arg)
        elif cmd == 'monitor':
            cmd_monitor()
        else:
            logger.info(f"\nUnknown command: {cmd}")
            logger.info("Commands:")
            logger.info("  snapshot [POST_ID]  Register existing comments")
            logger.info("  monitor             Start continuous monitoring")
            logger.info("  status              Show registry overview")
