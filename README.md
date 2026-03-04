# The Morning Train

A fully automated, AI-powered daily news digest covering AI, US Politics, World Politics, Financial Analysis, Movies & Shows, PC/PS5 Gaming, and Podcast Commentary. Centrist, fact-first analysis powered by Claude.

**Live at:** `https://<your-username>.github.io/the-morning-train/`

---

## Features

- **Balanced sourcing** — 70+ RSS feeds from across the political spectrum (AP, Reuters, NPR, WSJ, National Review, The Dispatch, Fox News, Reason, The Free Press, and more)
- **Podcast commentary** — Ezra Klein, The Daily, Hard Fork, Sam Harris, All-In, Bill Maher, Ben Shapiro, Megyn Kelly, Jonah Goldberg, Bari Weiss, Advisory Opinions
- **Verification levels** — Every story tagged as Confirmed, Developing, Disputed, or Analysis
- **Bias spectrum** — Visual indicator showing how left vs. right sources frame each political story
- **Wire Room** — Real-time live headlines panel powered by a Cloudflare Worker
- **Inquiry Desk** — Claude-powered multi-turn investigation of any story
- **Archive** — Date-based permalinks for every past edition
- **Personalization** — Learns your reading preferences via Cloudflare KV
- **Daily email** — Summary delivered via Resend.com
- **1920s aesthetic** — Train station / speakeasy / film noir visual design with analog clocks, art deco ornaments, and a noir cityscape footer

---

## How It Works

1. **GitHub Actions** runs daily at ~9-10 AM Eastern (14:00 UTC)
2. **RSS feeds** from 70+ curated sources are fetched
3. **Podcast episodes** from 13 shows across the political spectrum are pulled in
4. **Claude (Sonnet)** analyzes stories with balanced centrist commentary, assigns verification levels, and generates bias spectrum data
5. **HTML report** is generated and deployed to GitHub Pages
6. **Archive** — today's digest is copied to a date-based permalink
7. **Email summary** is sent via Resend (optional)
8. **Wire Room** button on the page fetches real-time headlines via Cloudflare Worker
9. **Inquiry Desk** lets you investigate any story deeper with Claude

---

## Quick Setup

See **DEPLOY.md** for the full step-by-step deployment guide (designed to be pasted into Claude Code).

### The short version:

1. Create a GitHub repo and push all files
2. Enable GitHub Pages (Source: GitHub Actions)
3. Add `ANTHROPIC_API_KEY` as a repo secret
4. Optionally add `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, `DIGEST_EMAIL` for email
5. Run the workflow: `gh workflow run "Daily News Digest"`
6. Deploy the Cloudflare Worker from `worker/` for live features
7. Optionally create a KV namespace for personalization

---

## Source Balance

| Lean | Count | Examples |
|------|-------|---------|
| Center-Left | 13 | NPR, BBC, Politico, Ezra Klein, NYT The Daily |
| Center | 14 | AP, Reuters, The Hill, RealClearPolitics, Sam Harris, All-In |
| Center-Right | 18 | WSJ, National Review, The Dispatch, Fox News, Reason, The Free Press, Ben Shapiro, Megyn Kelly |

---

## Cost Estimate

- **GitHub Actions:** Free (2,000 min/month)
- **Claude API:** ~$0.10-0.25 per digest (7 analyses + inquiry desk usage)
- **Resend:** Free tier (100 emails/day)
- **GitHub Pages:** Free
- **Cloudflare Worker:** Free tier (100K requests/day)
- **Cloudflare KV:** Free tier (100K reads/day, 1K writes/day)

**Total: roughly $3-8/month** in Claude API costs.

---

## Project Structure

```
the-morning-train/
├── .github/workflows/daily-digest.yml   # GitHub Actions automation
├── generate_digest.py                    # Main script (fetch → analyze → generate → archive → email)
├── requirements.txt                      # Python dependencies
├── templates/report.html                 # Jinja2 HTML template (1920s aesthetic)
├── worker/
│   ├── index.js                          # Cloudflare Worker (Wire Room + Inquiry Desk + Personalization)
│   └── wrangler.toml                     # Worker config
├── docs/
│   ├── index.html                        # Latest digest (deployed to Pages)
│   └── archive/                          # Date-based archive of past digests
│       ├── index.html                    # Archive listing page
│       └── YYYY/MM/YYYY-MM-DD.html      # Individual archived digests
├── README.md
└── DEPLOY.md                             # Full deployment prompt for Claude Code
```
