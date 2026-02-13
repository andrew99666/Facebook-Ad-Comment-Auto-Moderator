🔧 Auto-Hide New Comments on Facebook Ads (Open-Source Script)
Problem everyone knows: You seed your ad posts with 20 great comments for social proof — then random organic comments start burying them or cluttering the post. Manually hiding comments across 30+ creatives? Not scalable.
What this does: A Python script that monitors all your active ad posts and automatically hides any new comments that weren't part of your original seed. Your 20 seeded comments + your page replies stay visible. Everything else gets hidden within minutes.
How it works:

You seed your dark post with comments as usual
Run python moderator.py snapshot — registers every existing comment as "approved"
Run python moderator.py monitor — scans every ~10 minutes, hides anything new
That's it. Runs in the background on any machine

Key details:

Works with dark posts and shared creatives (multiple ads → same post)
Doesn't count comments (Facebook's API is unreliable for that) — instead it knows your seeded comments by ID and hides everything else
Auto-discovers all active ad posts from your ad account
Rate-limit aware — stays under Facebook's 200 calls/hour cap
Has a dry-run mode so you can verify before going live
Handles the known Facebook API issue where ~25% of comments are "invisible" — the script can't see them, but it also can't accidentally hide your seeds because of it

What you need:

Python 3.8+
A Facebook App with basic permissions
A User Access Token (not Page token — this matters)
Runs on any machine — your laptop, a $5 VPS, wherever


⚙️ Implementation Guide
Step 1 — Facebook App Setup (10 min)
Go to developers.facebook.com, create an app (type: Business). You need these permissions:
Copyads_read
pages_show_list
pages_read_engagement
pages_read_user_content
pages_manage_posts
pages_manage_engagement
Step 2 — Generate the Right Token (5 min)
This is where most people get stuck. You need a User token, not a Page token.

Open Graph API Explorer
Select your user profile at the top (not your Page)
Add the permissions listed above
Generate token → click "ℹ️" to confirm it says type: USER
Extend to long-lived: use the token extension endpoint (lasts 60 days, or never-expiring with offline_access)


Why User, not Page? The Ads API rejects Page tokens entirely. The script takes your User token and auto-exchanges it for a Page token when it needs to read/hide comments. Dual-token system, handled automatically.

Step 3 — Configure the Script (2 min)
Open moderator.py, fill in three values:
pythonCopyACCESS_TOKEN = "your-user-token-here"
PAGE_ID = "your-page-id"
AD_ACCOUNT_ID = "your-ad-account-id"  # numbers only, no 'act_' prefix
To find your Page ID: Page → About → Page transparency, or from the URL.
To find your Ad Account ID: Ads Manager URL contains act_XXXXXXXXX.
Step 4 — Snapshot Your Seeded Posts (5 min)
After your comments are seeded on all posts:
bashCopypython moderator.py snapshot
This scans every active ad post in your account, reads all existing comments, and saves their IDs to known_comments.json. Run it once per batch of new creatives.
Check what got captured:
bashCopypython moderator.py status
You'll see something like:
Copy📋 Registry: 29 posts
   Post ID                                   IDs   API    FB  Snapshot
   ──────────────────────────────────────────────────────────────────
   921081844424797_1234567890                  32    32    40  2025-01-15T10:00
   921081844424797_0987654321                  30    30    40  2025-01-15T10:00
   ...
   Total: 924 registered IDs

Note: Facebook's API often shows ~75% of comments (known platform limitation). The missing ones are from accounts Facebook has internally restricted. This doesn't affect the script — if it can't see a comment, it can't hide it either, and it won't accidentally hide your seeds.

Step 5 — Start Monitoring
Test first with dry run (default):
bashCopypython moderator.py monitor
The log shows what would be hidden without doing it. Once you're comfortable:
pythonCopyDRY_RUN = False  # in the script config
Then run monitor again. It loops forever, scanning every ~10 minutes, hiding new comments as they appear.
Step 6 — Keep It Running
For a VPS / always-on server:
bashCopynohup python moderator.py monitor > /dev/null 2>&1 &
Or use screen, tmux, or set it up as a systemd service.
When You Launch New Creatives

Create dark posts
Seed comments
python moderator.py snapshot (merges with existing — safe to re-run)
Monitoring picks up the new posts automatically
