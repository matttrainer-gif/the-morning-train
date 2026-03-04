/**
 * Cloudflare Worker — The Morning Train
 *
 * Endpoints:
 *   GET  /              — Wire Room: fetches live RSS headlines
 *   POST /query         — Inquiry Desk: deep-dives into a story via Claude API
 *   GET  /prefs/:token  — Get user preferences (personalization)
 *   POST /prefs/:token  — Save user preferences (personalization)
 *   POST /track/:token  — Track reading engagement (personalization)
 *
 * Deploy: `npx wrangler deploy`
 * Secrets needed: ANTHROPIC_API_KEY (for /query only)
 * KV namespace: PREFS (for personalization — optional)
 */

// ---------------------------------------------------------------------------
// Feed Configuration
// ---------------------------------------------------------------------------

const LIVE_FEEDS = {
  breaking: [
    { name: "AP News – Top", url: "https://rsshub.app/apnews/topics/apf-topnews" },
    { name: "Reuters – Top", url: "https://www.reutersagency.com/feed/?best-topics=top-news&post_type=best" },
    { name: "BBC – Breaking", url: "https://feeds.bbci.co.uk/news/rss.xml" },
    { name: "NPR – News Now", url: "https://feeds.npr.org/1001/rss.xml" },
    { name: "Fox News – Latest", url: "https://moxie.foxnews.com/google-publisher/latest.xml" },
    { name: "WSJ – US News", url: "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml" },
  ],
  politics: [
    { name: "AP – Politics", url: "https://rsshub.app/apnews/topics/politics" },
    { name: "The Hill", url: "https://thehill.com/feed/" },
    { name: "RealClearPolitics", url: "https://www.realclearpolitics.com/index.xml" },
    { name: "Politico", url: "https://www.politico.com/rss/politicopicks.xml" },
    { name: "BBC – US/Canada", url: "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml" },
    { name: "National Review", url: "https://www.nationalreview.com/feed/" },
    { name: "The Dispatch", url: "https://thedispatch.com/feed/" },
    { name: "Fox News – Politics", url: "https://moxie.foxnews.com/google-publisher/politics.xml" },
    { name: "Reason", url: "https://reason.com/feed/" },
    { name: "The Free Press", url: "https://www.thefp.com/feed" },
  ],
  world: [
    { name: "AP – World", url: "https://rsshub.app/apnews/topics/world-news" },
    { name: "Reuters – World", url: "https://www.reutersagency.com/feed/?best-topics=world&post_type=best" },
    { name: "BBC – World", url: "https://feeds.bbci.co.uk/news/world/rss.xml" },
    { name: "Al Jazeera", url: "https://www.aljazeera.com/xml/rss/all.xml" },
    { name: "WSJ – World", url: "https://feeds.a.dj.com/rss/RSSWorldNews.xml" },
  ],
  finance: [
    { name: "CNBC – Markets", url: "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258" },
    { name: "MarketWatch", url: "https://feeds.marketwatch.com/marketwatch/topstories/" },
    { name: "Yahoo Finance", url: "https://finance.yahoo.com/news/rssindex" },
    { name: "WSJ – Markets", url: "https://feeds.a.dj.com/rss/RSSMarketsMain.xml" },
  ],
  tech: [
    { name: "TechCrunch", url: "https://techcrunch.com/feed/" },
    { name: "The Verge", url: "https://www.theverge.com/rss/index.xml" },
    { name: "Ars Technica", url: "https://feeds.arstechnica.com/arstechnica/index" },
    { name: "Reason – Tech", url: "https://reason.com/tag/technology/feed/" },
  ],
  reddit: [
    { name: "r/news", url: "https://www.reddit.com/r/news/top/.rss?t=day&limit=10" },
    { name: "r/worldnews", url: "https://www.reddit.com/r/worldnews/top/.rss?t=day&limit=10" },
    { name: "r/technology", url: "https://www.reddit.com/r/technology/top/.rss?t=day&limit=10" },
    { name: "r/politics", url: "https://www.reddit.com/r/politics/top/.rss?t=day&limit=8" },
    { name: "r/conservative", url: "https://www.reddit.com/r/conservative/top/.rss?t=day&limit=8" },
    { name: "r/libertarian", url: "https://www.reddit.com/r/libertarian/top/.rss?t=day&limit=8" },
  ],
};

// ---------------------------------------------------------------------------
// RSS Parser
// ---------------------------------------------------------------------------

function parseRSSItems(xmlText, sourceName) {
  const items = [];
  const itemRegex = /<(?:item|entry)[\s>]([\s\S]*?)<\/(?:item|entry)>/gi;
  let match;

  while ((match = itemRegex.exec(xmlText)) !== null) {
    const block = match[1];
    const title = extractTag(block, "title");
    const link = extractLink(block);
    const pubDate = extractTag(block, "pubDate") || extractTag(block, "published") || extractTag(block, "updated") || "";
    const description = stripHTML(extractTag(block, "description") || extractTag(block, "summary") || extractTag(block, "content") || "");

    if (title) {
      items.push({
        title: stripHTML(title).trim(),
        link: link || "",
        source: sourceName,
        published: pubDate,
        timestamp: pubDate ? new Date(pubDate).getTime() : 0,
        summary: description.slice(0, 300).trim(),
      });
    }
  }
  return items;
}

function extractTag(block, tagName) {
  const cdataRegex = new RegExp(`<${tagName}[^>]*>\\s*<!\\[CDATA\\[([\\s\\S]*?)\\]\\]>\\s*</${tagName}>`, "i");
  const cdataMatch = block.match(cdataRegex);
  if (cdataMatch) return cdataMatch[1];
  const regex = new RegExp(`<${tagName}[^>]*>([\\s\\S]*?)</${tagName}>`, "i");
  const match = block.match(regex);
  return match ? match[1] : null;
}

function extractLink(block) {
  const atomLink = block.match(/<link[^>]+href=["']([^"']+)["'][^>]*\/?>/i);
  if (atomLink) return atomLink[1];
  return extractTag(block, "link") || "";
}

function stripHTML(str) {
  return (str || "").replace(/<[^>]+>/g, "").replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/\s+/g, " ");
}

// ---------------------------------------------------------------------------
// Feed Fetching
// ---------------------------------------------------------------------------

async function fetchFeed(feed, signal) {
  try {
    const resp = await fetch(feed.url, {
      headers: { "User-Agent": "TheMorningTrain-Wire/1.0" },
      signal,
    });
    if (!resp.ok) return [];
    const text = await resp.text();
    return parseRSSItems(text, feed.name);
  } catch (e) {
    console.error(`Failed: ${feed.name} — ${e.message}`);
    return [];
  }
}

async function fetchCategory(feeds) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const results = await Promise.allSettled(
      feeds.map((f) => fetchFeed(f, controller.signal))
    );
    const items = results
      .filter((r) => r.status === "fulfilled")
      .flatMap((r) => r.value);
    items.sort((a, b) => b.timestamp - a.timestamp);
    return dedup(items);
  } finally {
    clearTimeout(timeout);
  }
}

function dedup(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = item.title.toLowerCase().replace(/[^a-z0-9]/g, "").slice(0, 40);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

// ---------------------------------------------------------------------------
// Inquiry Desk — Claude-powered story deep-dive
// ---------------------------------------------------------------------------

const INQUIRY_SYSTEM_PROMPT = `You are an investigative research assistant for The Morning Train, a centrist daily news digest. The reader is looking at a story in their digest and wants to dig deeper.

YOUR ROLE:
- Help the reader understand the underlying truth of the story
- Separate confirmed facts from disputed claims, spin, and opinion
- Provide historical context and background the summary may have omitted
- Identify what's NOT being said — blind spots, missing perspectives, omitted data
- When relevant, note how the left and right interpret the same facts differently
- If the reader asks a specific question, answer it directly and thoroughly
- Cite your reasoning — explain HOW you know something, not just what you think

STYLE:
- Be direct, substantive, and analytical
- Write in clear prose paragraphs, not bullet points
- If you're uncertain about something, say so explicitly
- Don't hedge excessively — take positions when the evidence warrants it
- Treat the reader as intelligent and well-informed

BALANCE:
- Apply the same centrist, fact-first stance as the digest itself
- Don't default to left-leaning framing as neutral
- Present strong arguments from both sides when they exist
- When one side has the facts, say so clearly`;

async function handleQuery(request, env) {
  const apiKey = env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return { error: "ANTHROPIC_API_KEY not configured. Add it as a Worker secret." };
  }

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return { error: "Invalid JSON body" };
  }

  const { headline, analysis, key_facts, sources, question, conversation } = body;

  if (!headline || !question) {
    return { error: "Missing required fields: headline, question" };
  }

  // Build the story context
  let storyContext = `STORY HEADLINE: ${headline}\n`;
  if (analysis) storyContext += `\nDIGEST ANALYSIS:\n${analysis}\n`;
  if (key_facts && key_facts.length) storyContext += `\nKEY FACTS:\n${key_facts.map(f => `- ${f}`).join("\n")}\n`;
  if (sources && sources.length) storyContext += `\nSOURCES: ${sources.map(s => s.name || s).join(", ")}\n`;

  // Build messages — support multi-turn conversation
  const messages = [];

  // Add conversation history if present
  if (conversation && Array.isArray(conversation)) {
    for (const turn of conversation) {
      messages.push({ role: turn.role, content: turn.content });
    }
  }

  // Add the new question
  const userMessage = messages.length === 0
    ? `Here is a story from today's Morning Train digest:\n\n${storyContext}\n\nMy question: ${question}`
    : question;

  messages.push({ role: "user", content: userMessage });

  try {
    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: "claude-sonnet-4-20250514",
        max_tokens: 1500,
        system: INQUIRY_SYSTEM_PROMPT,
        messages,
      }),
    });

    if (!resp.ok) {
      const errText = await resp.text();
      console.error(`Claude API error: ${resp.status} ${errText}`);
      return { error: `Claude API returned ${resp.status}` };
    }

    const data = await resp.json();
    const reply = data.content?.[0]?.text || "No response received.";

    return {
      reply,
      usage: {
        input_tokens: data.usage?.input_tokens || 0,
        output_tokens: data.usage?.output_tokens || 0,
      },
    };
  } catch (e) {
    console.error(`Query error: ${e.message}`);
    return { error: `Failed to reach Claude API: ${e.message}` };
  }
}

// ---------------------------------------------------------------------------
// Personalization — Cloudflare KV based reading preferences
// ---------------------------------------------------------------------------

/**
 * Preferences stored per user token:
 * {
 *   category_weights: { "AI & Technology": 1.2, "US Politics": 1.5, ... },
 *   read_history: [ { category: "...", timestamp: ..., story_headline: "..." }, ... ],
 *   created_at: "ISO date",
 *   updated_at: "ISO date"
 * }
 */

const DEFAULT_PREFS = {
  category_weights: {
    "AI & Technology": 1.0,
    "US Politics": 1.0,
    "World Politics": 1.0,
    "Financial Analysis": 1.0,
    "Movies & Shows": 1.0,
    "PC & PS5 Gaming": 1.0,
    "Podcast Commentary": 1.0,
  },
  read_history: [],
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

async function handleGetPrefs(token, env) {
  if (!env.PREFS) {
    return { error: "KV namespace PREFS not bound. Add it to wrangler.toml." };
  }
  const prefs = await env.PREFS.get(`user:${token}`, { type: "json" });
  return prefs || { ...DEFAULT_PREFS, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
}

async function handleSavePrefs(token, request, env) {
  if (!env.PREFS) {
    return { error: "KV namespace PREFS not bound. Add it to wrangler.toml." };
  }

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return { error: "Invalid JSON body" };
  }

  // Merge with existing prefs
  const existing = await env.PREFS.get(`user:${token}`, { type: "json" }) || { ...DEFAULT_PREFS };

  if (body.category_weights) {
    existing.category_weights = { ...existing.category_weights, ...body.category_weights };
  }

  existing.updated_at = new Date().toISOString();

  await env.PREFS.put(`user:${token}`, JSON.stringify(existing));
  return existing;
}

async function handleTrackRead(token, request, env) {
  if (!env.PREFS) {
    return { error: "KV namespace PREFS not bound." };
  }

  let body;
  try {
    body = await request.json();
  } catch (e) {
    return { error: "Invalid JSON body" };
  }

  const { category, story_headline } = body;
  if (!category) {
    return { error: "Missing required field: category" };
  }

  const existing = await env.PREFS.get(`user:${token}`, { type: "json" }) || { ...DEFAULT_PREFS };

  // Add to read history (keep last 200 entries)
  existing.read_history = existing.read_history || [];
  existing.read_history.unshift({
    category,
    story_headline: story_headline || "",
    timestamp: new Date().toISOString(),
  });
  existing.read_history = existing.read_history.slice(0, 200);

  // Recalculate category weights based on reading frequency
  const catCounts = {};
  const totalReads = existing.read_history.length;
  for (const entry of existing.read_history) {
    catCounts[entry.category] = (catCounts[entry.category] || 0) + 1;
  }

  // Weight = base 1.0 + (0.5 * frequency ratio above average)
  const avgFreq = totalReads / 7; // 7 categories
  for (const [cat, count] of Object.entries(catCounts)) {
    const ratio = count / Math.max(avgFreq, 1);
    existing.category_weights[cat] = Math.round((0.5 + ratio * 0.5) * 100) / 100;
  }

  existing.updated_at = new Date().toISOString();
  await env.PREFS.put(`user:${token}`, JSON.stringify(existing));

  return { ok: true, weights: existing.category_weights };
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

export default {
  async fetch(request, env, ctx) {
    const corsHeaders = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };

    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    const url = new URL(request.url);
    const path = url.pathname;

    // ---- POST /query — Inquiry Desk ----
    if (request.method === "POST" && (path === "/query" || path === "/query/")) {
      const result = await handleQuery(request, env);
      const status = result.error ? (result.error.includes("Missing") ? 400 : 500) : 200;
      return new Response(JSON.stringify(result), {
        status,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // ---- GET /prefs/:token — Get preferences ----
    const prefsGetMatch = path.match(/^\/prefs\/([a-zA-Z0-9_-]+)\/?$/);
    if (request.method === "GET" && prefsGetMatch) {
      const result = await handleGetPrefs(prefsGetMatch[1], env);
      const status = result.error ? 500 : 200;
      return new Response(JSON.stringify(result), {
        status,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // ---- POST /prefs/:token — Save preferences ----
    const prefsSaveMatch = path.match(/^\/prefs\/([a-zA-Z0-9_-]+)\/?$/);
    if (request.method === "POST" && prefsSaveMatch) {
      const result = await handleSavePrefs(prefsSaveMatch[1], request, env);
      const status = result.error ? 500 : 200;
      return new Response(JSON.stringify(result), {
        status,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // ---- POST /track/:token — Track reading engagement ----
    const trackMatch = path.match(/^\/track\/([a-zA-Z0-9_-]+)\/?$/);
    if (request.method === "POST" && trackMatch) {
      const result = await handleTrackRead(trackMatch[1], request, env);
      const status = result.error ? 500 : 200;
      return new Response(JSON.stringify(result), {
        status,
        headers: { ...corsHeaders, "Content-Type": "application/json" },
      });
    }

    // ---- GET / — Wire Room (live feeds) ----
    if (request.method === "GET") {
      const category = url.searchParams.get("category") || "all";
      const limit = Math.min(parseInt(url.searchParams.get("limit") || "25"), 50);

      let feedsToFetch = [];
      if (category === "all") {
        feedsToFetch = Object.values(LIVE_FEEDS).flat();
      } else if (LIVE_FEEDS[category]) {
        feedsToFetch = LIVE_FEEDS[category];
      } else {
        return new Response(
          JSON.stringify({ error: `Unknown category: ${category}. Available: ${Object.keys(LIVE_FEEDS).join(", ")}, all` }),
          { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
      }

      const items = await fetchCategory(feedsToFetch);
      const response = {
        generated_at: new Date().toISOString(),
        category,
        count: Math.min(items.length, limit),
        items: items.slice(0, limit),
      };

      return new Response(JSON.stringify(response, null, 2), {
        headers: {
          ...corsHeaders,
          "Content-Type": "application/json",
          "Cache-Control": "public, max-age=120",
        },
      });
    }

    return new Response("Not found", { status: 404, headers: corsHeaders });
  },
};
