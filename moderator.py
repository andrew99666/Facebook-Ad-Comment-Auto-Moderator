"""
Facebook Ad Comment Auto-Moderator
Hides all comments on active ad posts. Runs on a fixed interval.
"""

import requests
import time
import signal
import sys
import logging
from datetime import datetime

# ================= CONFIGURATION =================
ACCESS_TOKEN = "XXX"
PAGE_ID = "XXX"
AD_ACCOUNT_ID = "XXX"
API_VERSION = "v21.0"

CHECK_INTERVAL = 300            # Seconds between scans (5 min)
DRY_RUN = False                 # True = log only, False = real hides
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
        logger.info(f"  > Ads API (act_{AD_ACCOUNT_ID})...")
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

    logger.info("  > Fallback: /ads_posts...")
    ids = fetch_paginated(
        url=f"https://graph.facebook.com/{API_VERSION}/{PAGE_ID}/ads_posts",
        params={'limit': 100, 'fields': 'id'},
        extract_fn=lambda p: p.get('id'),
        label="ads_posts", use_page_token=True
    )
    if ids:
        return list(ids)

    logger.info("  > Fallback: /published_posts...")
    ids = fetch_paginated(
        url=f"https://graph.facebook.com/{API_VERSION}/{PAGE_ID}/published_posts",
        params={'limit': 100, 'fields': 'id'},
        extract_fn=lambda p: p.get('id'),
        label="published_posts", use_page_token=True
    )
    return list(ids)


# ============== COMMENT HIDING ==============

COMMENT_FIELDS = 'id,is_hidden,message,from,parent'


def hide_post_comments(post_id):
    """Fetch all visible comments and hide any that aren't from the page."""
    page_id_str = str(PAGE_ID)
    url = f"https://graph.facebook.com/{API_VERSION}/{post_id}/comments"
    params = {
        'limit': 500,
        'fields': COMMENT_FIELDS,
        'filter': 'stream',
        'include_hidden': 'true',
    }

    comments = []
    while True:
        res = safe_request(requests.get, url, params=params, use_page_token=True)
        if 'error' in res:
            logger.warning(f"  ❌ {post_id}: {res['error'].get('message', 'Unknown')}")
            return
        for c in res.get('data', []):
            if c.get('id'):
                comments.append(c)
        if 'paging' in res and 'next' in res['paging']:
            url = res['paging']['next']
            params = {}
        else:
            break

    to_hide = [
        c for c in comments
        if not (str(c.get('from', {}).get('id')) == page_id_str or c.get('is_hidden'))
    ]

    icon = "🔴" if to_hide else "✅"
    logger.info(f"  {icon} {post_id}: visible={len(comments)} to_hide={len(to_hide)}")

    if not to_hide:
        return

    hidden_ok = 0
    hidden_fail = 0
    for comment in to_hide:
        msg = comment.get('message', '').replace('\n', ' ')[:50]
        ctype = "reply" if 'parent' in comment else "comment"
        if DRY_RUN:
            logger.info(f"    [DRY] Would hide {ctype}: \"{msg}\"")
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


# ============== MAIN LOOP ==============

def run():
    if DRY_RUN:
        logger.info("\n⚠️  DRY RUN MODE — nothing will be hidden")
    else:
        logger.info("\n🚨 LIVE MODE — comments WILL be hidden")
    logger.info(f"  Interval: {CHECK_INTERVAL}s ({CHECK_INTERVAL/60:.0f}m)\n")

    while True:
        start = datetime.now()
        logger.info(f"\n{'='*55}")
        logger.info(f"[{start.strftime('%H:%M:%S')}] Scan")

        try:
            active_posts = get_active_ad_posts()
        except Exception as e:
            logger.error(f"  ❌ Failed to get posts: {e}")
            time.sleep(60)
            continue

        logger.info(f"  Found {len(active_posts)} posts")

        for post_id in active_posts:
            try:
                hide_post_comments(post_id)
            except Exception as e:
                logger.error(f"  ❌ Error on {post_id}: {e}")

        elapsed = (datetime.now() - start).seconds
        logger.info(
            f"\n  Done ({elapsed}s). "
            f"Next in {CHECK_INTERVAL//60}m {CHECK_INTERVAL%60}s"
        )
        time.sleep(CHECK_INTERVAL)


# ============== SETUP ==============

def setup():
    logger.info("=" * 55)
    logger.info("  Facebook Ad Comment Hider")
    logger.info("=" * 55)
    if not check_token_type():
        sys.exit(1)
    if not verify_page():
        sys.exit(1)
    upgrade_to_page_token()
    diagnose_ad_account()


if __name__ == "__main__":
    setup()
    run()
