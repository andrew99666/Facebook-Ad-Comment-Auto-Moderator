# 🛡️ Facebook Ad Comment Auto-Moderator

Automatically hide new organic comments on your Facebook ad posts — keep your seeded social proof visible, bury everything else.

Built for media buyers running paid social at scale.

---

## The Problem

You create dark posts, seed them with 20 handpicked comments, reply to each as your Page — and now you have 40 comments of perfect social proof driving CTR.

Then real users start commenting. Their comments push your seeds down, go off-topic, or worse — kill your conversion rate. You're managing 30+ creatives. Manual moderation doesn't scale.

## The Solution

This script monitors every active ad post in your account and automatically hides any comment that wasn't part of your original seed. Your seeded comments and Page replies stay visible. Everything else disappears within minutes.
You seed 20 comments → Script locks them in → New comments get auto-hidden
Copy
---

## Features

- **Registry-based detection** — doesn't count comments (Facebook's API is unreliable for that). Instead, it knows your seeded comments by ID and hides everything else
- **Works with dark posts** and shared creatives (multiple ads pointing to the same post)
- **Auto-discovers** all active ad posts from your ad account
- **Rate-limit aware** — auto-calculates scan intervals to stay under Facebook's 200 calls/hour cap
- **Dry-run mode** — verify what would be hidden before going live
- **Pre-snapshot safety net** — if a seeded comment was invisible during setup but appears later, the script auto-registers it instead of hiding it
- **Single file, minimal dependencies** — just Python + `requests`

---

## How It Works

### Architecture
Copy                 ┌─────────────────────────────────────────┐
                 │           known_comments.json           │
                 │  Post A: [comment_1, comment_2, ...]    │
                 │  Post B: [comment_8, comment_9, ...]    │
                 └────────────────┬────────────────────────┘
                                  │
     SNAPSHOT (one-time)          │         MONITOR (continuous)
     ────────────────             │         ───────────────────
     Fetch all comments ──► Register IDs    Fetch comments (single-pass)
     Multi-pass (thorough)                        │
                                                  ▼
                                           For each comment:
                                             From Page? ──► Skip
                                             In registry? ──► Skip
                                             Pre-snapshot? ──► Auto-register
                                             New? ──► HIDE
Copy
### Token Flow
User Access Token ──┬── Ads API (get active ad post IDs)
│
└── Exchange ──► Page Token ──┬── Read comments
└── Hide comments
Copy
The script requires a **User** token (not Page). It auto-exchanges for a Page token internally. The Ads API rejects Page tokens entirely — this is the #1 setup mistake.

### API Budget (29 posts)
Per scan:     29 calls (1 per post)
Interval:     auto-calculated → ~10 min
Per hour:     ~174 / 200 limit ✓
Buffer:       20 calls reserved for hides
Copy
---

## Prerequisites

- Python 3.8+
- A Facebook App ([developers.facebook.com](https://developers.facebook.com))
- A **User** Access Token with these permissions:

| Permission | Used For |
|---|---|
| `ads_read` | Discover active ad posts |
| `pages_show_list` | Access your Page |
| `pages_read_engagement` | Read comments |
| `pages_read_user_content` | Read user comments |
| `pages_manage_posts` | Comment operations |
| `pages_manage_engagement` | Hide comments |

---

## Setup

### 1. Install

```bash
git clone https://github.com/yourusername/fb-ad-moderator.git
cd fb-ad-moderator
pip install requests
2. Generate a User Access Token

⚠️ Must be USER type, not PAGE type. This is the most common setup issue.


Open Graph API Explorer
Select your app from the dropdown
Select your user profile at the top (not your Page)
Click "Add a Permission" → add all six permissions listed above
Click "Generate Access Token"
Verify token type:

CopyGET /debug_token?input_token={TOKEN}&access_token={TOKEN}
Response must show "type": "USER". If it says "PAGE", re-do step 3.

Extend to long-lived:

CopyGET /oauth/access_token?grant_type=fb_exchange_token
  &client_id={APP_ID}
  &client_secret={APP_SECRET}
  &fb_exchange_token={SHORT_LIVED_TOKEN}
3. Configure
Open moderator.py and fill in:
pythonCopyACCESS_TOKEN = "your-user-token"
PAGE_ID = "your-page-id"           # Page → About → Page ID
AD_ACCOUNT_ID = "1234567890"       # Numbers only, no 'act_' prefix
Finding your IDs:
ValueWhere to FindPage IDPage → About → Transparency → Page IDAd Account IDAds Manager URL: act_XXXXXXXXX (use the numbers only)
4. Snapshot Your Seeds
After all comments are seeded on your posts:
bashCopypython moderator.py snapshot
Verify the registry:
bashCopypython moderator.py status
Copy📋 Registry: 29 posts

   Post ID                                   IDs   API    FB  Snapshot
   ──────────────────────────────────────────────────────────────────────
   921081844424797_1234567890                  32    32    40  2025-01-15T10:00
   921081844424797_0987654321                  30    30    40  2025-01-15T10:00
   ...
   Total: 924 registered IDs

   Calls/cycle: ~29 | Cycles/hour: ~6 | Calls/hour: ~174 / 200
5. Start Monitoring
Dry run first (default — logs what would be hidden, hides nothing):
bashCopypython moderator.py monitor
Go live (edit the config):
pythonCopyDRY_RUN = False
bashCopypython moderator.py monitor

Usage
Commands
bashCopypython moderator.py snapshot              # Register all active ad posts
python moderator.py snapshot POST_ID      # Register a specific post
python moderator.py monitor               # Start continuous monitoring
python moderator.py status                # Show registry overview
Adding New Creatives
bashCopy# 1. Create dark posts + seed comments
# 2. Register them (merges with existing — safe to re-run)
python moderator.py snapshot
# 3. Monitor picks them up automatically on the next cycle
Running in Background
bashCopy# Using nohup
nohup python moderator.py monitor > /dev/null 2>&1 &

# Using screen
screen -S moderator
python moderator.py monitor
# Ctrl+A, D to detach

# Using tmux
tmux new -s moderator
python moderator.py monitor
# Ctrl+B, D to detach
Log Output
All activity logs to moderator.log and stdout:
Copy[14:30:00] Scan | 29 posts | budget: 187/200 | interval: 580s
  ✅ 921081844424797_001: known=28 page=12
  ✅ 921081844424797_002: known=27 page=12
  🔴 921081844424797_003: known=28 page=12 → NEW=2
    🚫 Hiding comment: "Is this legit?"
    🚫 Hiding reply: "I tried calling them..."
    📊 Hidden: 2 | Failed: 0
  ...
  Done (8s) | Budget: 158/200 | Next in 580s (9m 40s)

Configuration Reference
pythonCopy# Required
ACCESS_TOKEN = ""         # User access token (not Page)
PAGE_ID = ""              # Your Facebook Page ID
AD_ACCOUNT_ID = ""        # Ad account ID (numbers only)

# Optional
API_VERSION = "v21.0"     # Graph API version
DRY_RUN = True            # True = log only, False = actually hide
REGISTRY_FILE = "known_comments.json"

# Rate limits
PAGE_CALLS_PER_HOUR = 200 # Facebook's limit for Page tokens
CALLS_BUFFER = 20         # Reserved for hide operations
POSTS_CACHE_TTL = 1800    # Seconds between ad post list refresh
MIN_INTERVAL = 120        # Minimum seconds between scans
MAX_INTERVAL = 900        # Maximum seconds between scans

FAQ
Why User token and not Page token?
Facebook's Ads API rejects Page tokens entirely. The script needs the Ads API to discover which posts have active ads. It auto-exchanges your User token for a Page token when reading/hiding comments.
Why does the status show fewer comments than expected?
Facebook's API consistently returns ~75% of comments. The missing ones are from accounts Facebook has internally restricted. This is a known platform limitation with no workaround.
This doesn't break anything. If the script can't see a comment, it can't hide it — but it also can't accidentally hide your seeds.
What if I seed more comments later?
Run snapshot again. It merges with the existing registry — never removes previously registered IDs. Safe to run as many times as you want.
What about replies to seeded comments from real users?
Hidden. Any comment not in the registry and not from your Page gets hidden — whether it's a new top-level comment or a reply to one of your seeds.
What if a seeded comment was invisible during snapshot but appears later?
The script checks the comment's created_time. If it was created before your first snapshot, it's almost certainly a seeded comment that was invisible at the time. The script auto-registers it instead of hiding it.
Will hiding comments get my ad account flagged?
Hiding is a native Facebook moderation feature available to all Page admins. It doesn't delete comments — the commenter can still see their own comment. Facebook does not penalize Pages for using their own moderation tools.
How many ad posts can this handle?
The rate limit is the bottleneck. At 200 API calls/hour:
PostsCalls/CycleIntervalCycles/Hour1010~3 min~182929~10 min~65050~17 min~3100100~33 min~2
The script auto-calculates the interval. More posts = longer between scans.

Known Limitations
LimitationImpactWorkaroundFacebook API returns ~75% of commentsSome seeded comments invisible to scriptCan't see them = can't accidentally hide them. Run snapshot multiple times to catch more.200 API calls/hour (Page token)Scan frequency limited by post countAuto-calculated intervals. For 100+ posts, consider multiple Pages/apps.Token expirationLong-lived tokens last ~60 daysSet a calendar reminder to regenerate. Script logs clear errors on token expiry.

License
MIT

Contributing
Issues and PRs welcome. If you hit a Facebook API edge case not covered here, open an issue with the error from moderator.log.
