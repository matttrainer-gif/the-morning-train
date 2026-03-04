# The Morning Train — Full Deployment Prompt

Copy everything below the line into Claude Code in your terminal.

---

## PROMPT TO PASTE INTO CLAUDE CODE:

```
I have a complete project called "The Morning Train" — a daily news digest platform — ready to deploy. The project files are in this directory. I need you to stand up the entire stack. Here is what needs to happen, step by step:

## OVERVIEW

The Morning Train is an automated daily news digest that:
- Fetches RSS feeds from 70+ balanced sources (left, center, right)
- Uses Claude Sonnet to analyze and synthesize stories with centrist, fact-first commentary
- Generates a styled HTML report with verification levels and bias spectrum indicators
- Deploys to GitHub Pages (free, always-on hosting)
- Sends a daily email summary via Resend.com
- Includes a live "Wire Room" for real-time headlines via a Cloudflare Worker
- Includes an "Inquiry Desk" to investigate stories deeper via Claude API
- Archives past digests with date-based permalinks
- Has a personalization layer via Cloudflare KV that learns reading preferences

## STEP 1: CREATE GITHUB REPO

Create a new GitHub repository called `the-morning-train` (or `daily-news-digest`):

```bash
gh repo create the-morning-train --public --description "The Morning Train — Automated daily news digest with centrist AI analysis" --clone
```

Copy all project files into the repo directory. The file structure should be:

```
the-morning-train/
├── .github/workflows/daily-digest.yml
├── generate_digest.py
├── requirements.txt
├── templates/report.html
├── worker/
│   ├── index.js
│   └── wrangler.toml
├── docs/index.html
├── README.md
└── DEPLOY.md
```

Stage and commit everything:
```bash
git add -A
git commit -m "Initial commit — The Morning Train"
git push -u origin main
```

## STEP 2: ENABLE GITHUB PAGES

```bash
# Enable Pages with GitHub Actions as the source
gh api repos/{owner}/{repo}/pages -X POST -f build_type=workflow --silent 2>/dev/null || echo "Pages may already be enabled"
```

If that doesn't work, tell me to go to Settings → Pages → set Source to "GitHub Actions" manually.

## STEP 3: ADD GITHUB SECRETS

I need the following secrets added. Ask me for each value:

1. **ANTHROPIC_API_KEY** (REQUIRED) — My Anthropic API key for Claude analysis
2. **RESEND_API_KEY** (optional) — For daily email delivery. Get one free at resend.com
3. **RESEND_FROM_EMAIL** (optional) — Verified sender email in Resend (e.g., digest@mydomain.com, or use onboarding@resend.dev for testing)
4. **DIGEST_EMAIL** (optional) — Email address to receive the daily digest (matt.trainer@gmail.com)

Set them with:
```bash
gh secret set ANTHROPIC_API_KEY --body "sk-ant-..."
gh secret set RESEND_API_KEY --body "re_..."
gh secret set RESEND_FROM_EMAIL --body "digest@..."
gh secret set DIGEST_EMAIL --body "matt.trainer@gmail.com"
```

## STEP 4: TEST THE GITHUB ACTION

Trigger the workflow manually to verify everything works:

```bash
gh workflow run "Daily News Digest" --ref main
```

Watch it run:
```bash
gh run list --workflow="Daily News Digest" --limit 1
# Then watch the specific run:
gh run watch
```

If it succeeds, the digest should be live at:
`https://<username>.github.io/the-morning-train/`

## STEP 5: DEPLOY CLOUDFLARE WORKER

This powers the Wire Room (live headlines) and Inquiry Desk (story deep-dives).

```bash
cd worker

# Install wrangler if not already installed
npm install -g wrangler

# Login to Cloudflare (this opens a browser)
wrangler login

# Deploy the worker
npx wrangler deploy
```

This will output a URL like `https://morning-train.<subdomain>.workers.dev`. Save this URL.

Now add the Anthropic API key as a Worker secret (for the Inquiry Desk):
```bash
npx wrangler secret put ANTHROPIC_API_KEY
# Paste your API key when prompted
```

## STEP 6: SET UP PERSONALIZATION KV (OPTIONAL)

Create a KV namespace for storing reading preferences:

```bash
npx wrangler kv namespace create PREFS
```

This outputs something like:
```
{ binding = "PREFS", id = "abc123..." }
```

Edit `wrangler.toml` and uncomment the KV namespace section, replacing the ID:

```toml
[[kv_namespaces]]
binding = "PREFS"
id = "abc123..."   # ← paste the actual ID here
```

Then redeploy:
```bash
npx wrangler deploy
```

Go back to the repo root:
```bash
cd ..
```

## STEP 7: CONFIGURE WORKER URL IN THE TEMPLATE

Edit `templates/report.html` and find this line near the bottom of the JavaScript:
```js
const WORKER_URL = window.LIVE_WORKER_URL || localStorage.getItem("digest_worker_url") || "";
```

There are TWO occurrences (one for Wire Room, one for Inquiry Desk). Replace the empty string `""` in BOTH with your Worker URL:
```js
const WORKER_URL = window.LIVE_WORKER_URL || localStorage.getItem("digest_worker_url") || "https://morning-train.<subdomain>.workers.dev";
```

Commit and push:
```bash
git add templates/report.html
git commit -m "Configure Cloudflare Worker URL"
git push
```

## STEP 8: VERIFY EVERYTHING

1. **Check the live site**: Open `https://<username>.github.io/the-morning-train/`
2. **Test Wire Room**: Click the "WIRE ROOM" button in the nav. It should fetch live headlines.
3. **Test Inquiry Desk**: Click "Investigate" on any story card. Ask a question. Claude should respond.
4. **Test Archive**: Click "Archive" in the nav. Should show the archive index (will populate after first run).
5. **Check email**: If you configured Resend, you should have received the digest email.

## STEP 9: SET UP RESEND EMAIL (if not already done)

1. Go to https://resend.com and sign up (free: 100 emails/day)
2. Get your API key from the dashboard
3. For production: verify your domain in Resend's DNS settings
4. For testing: you can use `onboarding@resend.dev` as the sender

## IMPORTANT NOTES

- The GitHub Action runs daily at 14:00 UTC (~9-10 AM Eastern). Edit the cron in `.github/workflows/daily-digest.yml` to change the schedule.
- Claude API costs ~$0.08-0.20 per daily digest run (7 category analyses using Sonnet).
- The Cloudflare Worker free tier handles 100K requests/day — more than enough.
- Cloudflare KV free tier: 100K reads/day, 1K writes/day — plenty for personal use.
- GitHub Pages is free. GitHub Actions free tier: 2,000 minutes/month.
- **Total monthly cost: ~$3-6** in Claude API usage.

## TROUBLESHOOTING

- If the GitHub Action fails, check the run logs: `gh run view --log-failed`
- If feeds aren't loading, some RSS feeds may be temporarily down — the script handles this gracefully
- If the Worker returns errors, check: `npx wrangler tail` (live logs)
- If email doesn't send, verify RESEND_API_KEY and RESEND_FROM_EMAIL are correct

Now execute these steps. Ask me for any API keys or credentials you need. Start with Step 1.
```
