#!/usr/bin/env node
/**
 * TechPulse — Static Build Script
 * ================================
 * Run after your article pipeline has updated articles.json.
 *
 * Generates:
 *   dist/index.html                    — pre-rendered homepage
 *   dist/article/{slug}.html           — one HTML file per article
 *   dist/category/{slug}.html          — one HTML file per category
 *   dist/about.html                    — about page
 *   dist/404.html                      — 404 page
 *   dist/sitemap.xml                   — full sitemap
 *   dist/news-sitemap.xml              — Google News sitemap (articles < 2 days old)
 *   dist/feed.xml                      — RSS 2.0 feed (with content:encoded)
 *   dist/robots.txt                    — crawl rules
 *   dist/articles.json                 — copied so Cloudflare can serve it
 *
 * Usage:
 *   node build.js
 *   node build.js --articles ./data/articles.json --out ./public
 *
 * Requirements: Node 18+, no npm dependencies.
 */

import fs   from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ── CONFIG ────────────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const getArg = (flag, fallback) => {
  const i = args.indexOf(flag);
  return i !== -1 && args[i + 1] ? args[i + 1] : fallback;
};

const SITE_URL        = getArg('--site',     'https://techpulse.example.com');
const ARTICLES_FILE   = getArg('--articles', path.join(__dirname, 'articles.json'));
const OUT_DIR         = getArg('--out',      path.join(__dirname, 'dist'));
const INDEX_HTML_FILE = getArg('--index',    path.join(__dirname, 'index.html'));
const SITE_NAME       = 'TechPulse';
const DEFAULT_DESC    = 'AI-curated tech news summaries updated every 12 hours. Clear, jargon-free briefings on AI, software, hardware, and more.';
const DEFAULT_IMG     = `${SITE_URL}/og-image.png`;
const TWITTER_HANDLE  = '@TechPulseAI';

// ── EXTRACT CSS & JS FROM INDEX.HTML ─────────────────────────────────────────
let INLINE_CSS = '';
let INLINE_JS  = '';

function extractAssetsFromIndex() {
  if (!fs.existsSync(INDEX_HTML_FILE)) {
    console.error(`❌ index.html not found at: ${INDEX_HTML_FILE}`);
    process.exit(1);
  }

  const indexContent = fs.readFileSync(INDEX_HTML_FILE, 'utf8');

  const styleMatch = indexContent.match(/<style[^>]*>([\s\S]*?)<\/style>/i);
  if (styleMatch) {
    INLINE_CSS = styleMatch[1].trim();
    console.log(`   ✓ Extracted ${INLINE_CSS.length.toLocaleString()} bytes of CSS from index.html`);
  } else {
    console.warn('   ⚠ No <style> tag found in index.html');
  }

  const scriptMatches = indexContent.match(/<script>([\s\S]*?)<\/script>/gi);
  if (scriptMatches) {
    for (const script of scriptMatches) {
      const contentMatch = script.match(/<script[^>]*>([\s\S]*?)<\/script>/i);
      if (contentMatch) {
        const content = contentMatch[1].trim();
        if (content.includes('SITE_URL') || content.includes('function init()') || content.includes('allArticles')) {
          INLINE_JS = content;
          console.log(`   ✓ Extracted ${INLINE_JS.length.toLocaleString()} bytes of JS from index.html`);
          break;
        }
      }
    }
  }

  if (!INLINE_JS) {
    console.warn('   ⚠ No main app script found in index.html');
  }
}

// ── HELPERS ───────────────────────────────────────────────────────────────────
function esc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function xmlEsc(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function slugify(title) {
  if (!title) return 'article';
  return title.toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-')
    .slice(0, 80);
}

function articleSlug(article) {
  return slugify(article.title);
}

function isoDate(str) {
  if (!str) return new Date().toISOString();
  const d = new Date(str);
  return isNaN(d) ? new Date().toISOString() : d.toISOString();
}

function rfcDate(str) {
  const d = str ? new Date(str) : new Date();
  return isNaN(d) ? new Date().toUTCString() : d.toUTCString();
}

function shortDate(str) {
  if (!str) return '';
  const d = new Date(str);
  if (isNaN(d)) return '';
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function stripMarkdown(md) {
  return (md || '')
    .replace(/#{1,6}\s+/g, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/`(.+?)`/g, '$1')
    .replace(/\[(.+?)\]\(.+?\)/g, '$1')
    .replace(/\n+/g, ' ')
    .trim();
}

function excerpt(article, maxLen = 155) {
  const raw = stripMarkdown(article.summary);
  return raw.length > maxLen ? raw.slice(0, maxLen - 1) + '…' : raw;
}

function write(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content, 'utf8');
  console.log(`  ✓ ${path.relative(OUT_DIR, filePath)}`);
}

// ── MARKDOWN → HTML (used for RSS content:encoded and pre-rendered bodies) ───
function basicMarkdownToHtml(md) {
  if (!md) return '';

  // Escape HTML entities first so raw content is safe
  let html = md
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // ── FIX: Ensure blank line after bold standalone headers ──
  html = html.replace(/^\*\*(.+?)\*\*\s*$/gm, '**$1**\n\n');

  // Headings (# style)
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm,  '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm,   '<h2>$1</h2>');

  // Inline formatting
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*(.+?)\*/g,     '<em>$1</em>');
  html = html.replace(/`(.+?)`/g,       '<code>$1</code>');

  // Unordered list items → wrap consecutive runs in <ul>
  html = html.replace(/^[ \t]*[-*]\s+(.+)$/gm, '<li>$1</li>');
  html = html.replace(/((?:<li>[^\n]*<\/li>\n?)+)/g, '<ul>$1</ul>');

  // Split on blank lines and wrap non-block-level lines in <p>
  const BLOCK = /^<(h[1-6]|ul|ol|li|blockquote|pre|div)/;
  const paragraphs = html.split(/\n{2,}/).map(block => {
    block = block.trim();
    if (!block) return '';
    if (BLOCK.test(block)) return block;
    return `<p>${block.replace(/\n/g, ' ')}</p>`;
  }).filter(Boolean);

  return paragraphs.join('\n');
}

// ── SHARED PAGE TEMPLATE ──────────────────────────────────────────────────────
/**
 * @param {Object} opts
 * @param {string} opts.title
 * @param {string} opts.description
 * @param {string} opts.canonicalPath
 * @param {string} [opts.ogType]
 * @param {string} [opts.ogImage]
 * @param {string} [opts.articleSchema]
 * @param {string} [opts.breadcrumbSchema]
 * @param {string} [opts.collectionSchema]
 * @param {string} [opts.bodyContent]
 * @param {string} [opts.preRenderedContent]
 * @param {Object} [opts.articleMeta]   — only on article pages
 * @param {string}   opts.articleMeta.author
 * @param {string}   opts.articleMeta.publishedTime  — ISO 8601
 * @param {string}   opts.articleMeta.modifiedTime   — ISO 8601
 * @param {string}   opts.articleMeta.section
 * @param {string}   opts.articleMeta.newsKeywords   — comma-separated
 * @param {string[]} opts.articleMeta.tags
 */
function pageShell({
  title, description, canonicalPath,
  ogType = 'website', ogImage = DEFAULT_IMG,
  articleSchema = '', breadcrumbSchema = '', collectionSchema = '',
  bodyContent, preRenderedContent = '',
  articleMeta = null
}) {
  const canonical = `${SITE_URL}${canonicalPath}`;

  // Build article-specific <meta> tags (only emitted on article pages)
  const articleMetaHtml = articleMeta ? `
  <meta name="author" content="${esc(articleMeta.author)}" />
  <meta name="news_keywords" content="${esc(articleMeta.newsKeywords)}" />
  <meta property="article:published_time" content="${esc(articleMeta.publishedTime)}" />
  <meta property="article:modified_time"  content="${esc(articleMeta.modifiedTime)}" />
  <meta property="article:author"  content="${esc(articleMeta.author)}" />
  <meta property="article:section" content="${esc(articleMeta.section)}" />
  ${(articleMeta.tags || []).map(t => `<meta property="article:tag" content="${esc(t)}" />`).join('\n  ')}` : '';

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="format-detection" content="telephone=no" />

  <!-- PRIMARY META -->
  <title>${esc(title)}</title>
  <meta name="description" content="${esc(description)}" />
  <meta name="robots" content="index, follow" />
  <link rel="canonical" href="${esc(canonical)}" />

  <!-- OPEN GRAPH -->
  <meta property="og:type"        content="${esc(ogType)}" />
  <meta property="og:site_name"   content="${esc(SITE_NAME)}" />
  <meta property="og:title"       content="${esc(title)}" />
  <meta property="og:description" content="${esc(description)}" />
  <meta property="og:url"         content="${esc(canonical)}" />
  <meta property="og:image"       content="${esc(ogImage)}" />
  <meta property="og:image:width"  content="1200" />
  <meta property="og:image:height" content="630" />

  <!-- TWITTER CARD -->
  <meta name="twitter:card"        content="summary_large_image" />
  <meta name="twitter:site"        content="${esc(TWITTER_HANDLE)}" />
  <meta name="twitter:title"       content="${esc(title)}" />
  <meta name="twitter:description" content="${esc(description)}" />
  <meta name="twitter:image"       content="${esc(ogImage)}" />
  ${articleMetaHtml}
  <!-- THEME COLOR -->
  <meta name="theme-color" content="#e07838" media="(prefers-color-scheme: dark)" />
  <meta name="theme-color" content="#d4641a" media="(prefers-color-scheme: light)" />

  <!-- FEEDS & DISCOVERY -->
  <link rel="alternate" type="application/rss+xml" title="${esc(SITE_NAME)} — Latest Articles" href="/feed.xml" />

  <!-- STRUCTURED DATA: always-present site-level schemas -->
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "Organization",
    "@id": "${SITE_URL}/#organization",
    "name": "${SITE_NAME}",
    "url": "${SITE_URL}",
    "logo": { "@type": "ImageObject", "url": "${SITE_URL}/logo.png" },
    "sameAs": ["https://twitter.com/TechPulseAI"]
  }
  </script>
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "WebSite",
    "@id": "${SITE_URL}/#website",
    "name": "${SITE_NAME}",
    "url": "${SITE_URL}",
    "publisher": { "@id": "${SITE_URL}/#organization" },
    "potentialAction": {
      "@type": "SearchAction",
      "target": "${SITE_URL}/?q={search_term_string}",
      "query-input": "required name=search_term_string"
    }
  }
  </script>
  ${articleSchema    ? `<script type="application/ld+json">\n  ${articleSchema}\n  </script>` : ''}
  ${breadcrumbSchema ? `<script type="application/ld+json">\n  ${breadcrumbSchema}\n  </script>` : ''}
  ${collectionSchema ? `<script type="application/ld+json">\n  ${collectionSchema}\n  </script>` : ''}

  <!-- DYNAMIC JSON-LD SLOTS (updated by SPA JS on client-side navigation) -->
  <script type="application/ld+json" id="jsonld-article"></script>
  <script type="application/ld+json" id="jsonld-breadcrumb"></script>

  <!-- PERFORMANCE -->
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="dns-prefetch" href="https://cdn.jsdelivr.net" />
  <link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=IBM+Plex+Sans:wght@300;400;500&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet" />
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js" defer></script>

  <!-- INLINED CSS (extracted from index.html) -->
  <style>${INLINE_CSS}</style>
</head>
<body>

<header>
  <div class="header-top">
    <a class="logo" id="logo" href="/" aria-label="TechPulse — Home">Tech<span>Pulse</span></a>
    <div class="header-right">
      <button class="search-trigger" id="search-trigger" aria-label="Search articles" aria-haspopup="dialog">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <span>Search</span>
      </button>

      <!-- ── LAYOUT TOGGLE ── -->
      <div class="layout-toggle" role="group" aria-label="Toggle card layout">
        <button class="layout-btn active" id="layout-list-btn" aria-label="List layout" aria-pressed="true" title="List view">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
            <line x1="1" y1="4"  x2="15" y2="4"/>
            <line x1="1" y1="8"  x2="15" y2="8"/>
            <line x1="1" y1="12" x2="15" y2="12"/>
          </svg>
        </button>
        <button class="layout-btn" id="layout-grid-btn" aria-label="Grid layout" aria-pressed="false" title="Grid view">
          <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <rect x="1"  y="1"  width="6" height="6" rx="1.5"/>
            <rect x="9"  y="1"  width="6" height="6" rx="1.5"/>
            <rect x="1"  y="9"  width="6" height="6" rx="1.5"/>
            <rect x="9"  y="9"  width="6" height="6" rx="1.5"/>
          </svg>
        </button>
      </div>

      <!-- ── THEME TOGGLE ── -->
      <label class="theme-toggle" id="theme-toggle" aria-label="Toggle light/dark theme">
        <input type="checkbox" id="theme-checkbox" hidden />
        <span class="toggle-track"><span class="toggle-thumb">
          <svg class="sun-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
          <svg class="moon-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
        </span></span>
      </label>

      <div class="live-badge" aria-label="Auto-updated every 12 hours">
        <div class="live-dot" aria-hidden="true"></div>
        <span>AUTO-UPDATED</span>
      </div>
    </div>
  </div>
  <div class="header-categories">
    <nav id="nav" aria-label="Main navigation"></nav>
  </div>
</header>

<!-- SEARCH OVERLAY -->
<div id="search-overlay" role="dialog" aria-modal="true" aria-label="Search articles" aria-hidden="true">
  <div class="search-box">
    <div class="search-input-row">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
      <input id="search-input" type="search" placeholder="Search articles, sources, authors…" autocomplete="off" spellcheck="false" aria-label="Search query" />
      <div class="search-shortcut" aria-hidden="true"><kbd>⌘</kbd><kbd>K</kbd></div>
      <button class="search-close" id="search-close" aria-label="Close search">ESC</button>
    </div>
    <div class="search-filters" id="search-filters" role="group" aria-label="Search filters">
      <span class="filter-label">Filter:</span>
      <button class="filter-chip active" data-filter="all" aria-pressed="true">All fields</button>
      <button class="filter-chip" data-filter="title" aria-pressed="false">Title</button>
      <button class="filter-chip" data-filter="author" aria-pressed="false">Author</button>
      <button class="filter-chip" data-filter="source" aria-pressed="false">Source</button>
      <button class="filter-chip" data-filter="date" aria-pressed="false">Date</button>
    </div>
    <div id="search-results" role="listbox" aria-label="Search results">
      <div class="search-hint">Type to search across all articles</div>
    </div>
  </div>
</div>

<main id="main">
  <!-- PRE-RENDERED CONTENT (visible to crawlers; hidden once JS hydrates) -->
  <div id="prerendered-content">
    ${preRenderedContent}
  </div>

  <!-- SPA ROOT (hidden until JS loads articles.json successfully) -->
  <div id="spa-root" style="display:none;">
    <div id="loading"><div class="spinner" aria-hidden="true"></div>Loading…</div>
    <div id="error-msg" style="display:none;" role="alert">Could not load articles.json.</div>
    <div id="list-view" style="display:none;">
      <nav class="breadcrumb" id="list-breadcrumb" aria-label="Breadcrumb"></nav>
      <div class="page-header">
        <h1 class="page-title" id="page-title">Latest News</h1>
        <p class="page-subtitle" id="page-subtitle"></p>
      </div>
      <div id="groups-container"></div>
    </div>
    <div id="article-view">
      <nav class="breadcrumb" id="article-breadcrumb" aria-label="Breadcrumb"></nav>
      <div class="article-header">
        <div class="article-category-badge" id="art-badge"></div>
        <h1 class="article-title" id="art-title"></h1>
        <address class="article-byline" id="art-byline"></address>
      </div>
      <div class="article-body" id="art-body"></div>
      <div class="source-box">
        <div class="source-box-meta" id="art-source-meta"></div>
        <a class="read-original" id="art-link" target="_blank" rel="noopener noreferrer">
          Read original
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="7" y1="17" x2="17" y2="7"/><polyline points="7 7 17 7 17 17"/></svg>
        </a>
      </div>
      <section class="related-section" id="related-section" aria-label="Related articles">
        <h2>Related Articles</h2>
        <div class="related-list" id="related-list"></div>
      </section>
    </div>
    <div id="about-view">
      <nav class="breadcrumb" aria-label="Breadcrumb">
        <a href="/">TechPulse</a><span class="sep" aria-hidden="true">›</span>
        <span class="current" aria-current="page">About</span>
      </nav>
      <div class="about-wrap">
        <p class="about-kicker">About TechPulse</p>
        <h1 class="about-hero-title">The tech news feed<br>that never <em>sleeps.</em></h1>
        <p class="about-lead">TechPulse is a fully automated, AI-run tech news blog. It scours the web for the latest technology stories, then summarizes them into clear, jargon-free briefings — so you get the signal without the noise.</p>
        <div class="about-body">
          <p>While you're sleeping, TechPulse is reading. Hundreds of articles from across the internet, distilled into the ones that actually matter — no filler, no sponsored fluff, no doomscrolling required.</p>
          <p>This isn't a newsletter you forgot to unsubscribe from. It's a living, breathing feed of what's happening right now in tech — curated by AI so a human doesn't have to.</p>
          <p>Categories, sources, and topics are all driven by a single config file — so the feed evolves without touching a line of HTML.</p>
        </div>
        <div class="about-divider"></div>
        <div class="about-stats">
          <div class="stat-card"><div class="stat-number" id="stat-total">—</div><div class="stat-label">Articles Summarized</div></div>
          <div class="stat-card"><div class="stat-number" id="stat-categories">—</div><div class="stat-label">Categories Tracked</div></div>
          <div class="stat-card"><div class="stat-number" id="stat-sources">—</div><div class="stat-label">Unique Sources</div></div>
          <div class="stat-card"><div class="stat-number">0</div><div class="stat-label">Coffees Drank</div><div class="stat-joke">i am a bot</div></div>
        </div>
        <div class="about-divider"></div>
        <div class="about-disclaimer"><strong>A note on accuracy:</strong> All summaries are generated by AI and may contain errors or omissions. TechPulse is a reading aid, not a primary news source.</div>
      </div>
    </div>
    <div id="notfound-view">
      <div class="notfound-code" aria-hidden="true">404</div>
      <h1 class="notfound-title">Page not found</h1>
      <p class="notfound-body">The article or page you're looking for doesn't exist.</p>
      <button class="notfound-home" id="notfound-home">← Back to latest news</button>
    </div>
  </div>
</main>

<footer>
  <p>
    ${esc(SITE_NAME)} — Summaries generated by AI
    &nbsp;·&nbsp; Updated every 12 hours
    &nbsp;·&nbsp; <span id="footer-count"></span>
    &nbsp;·&nbsp; <a href="/feed.xml">RSS Feed</a>
    &nbsp;·&nbsp; <a href="/sitemap.xml">Sitemap</a>
  </p>
</footer>

<!-- INLINED JS (extracted from index.html) -->
<script>${INLINE_JS}</script>
${bodyContent || ''}
</body>
</html>`;
}

// ── PRE-RENDERED CONTENT BUILDERS ────────────────────────────────────────────

function buildArticlePrerender(article, allArticles) {
  const related = allArticles
    .filter(a => a.url !== article.url && a.category === article.category)
    .slice(0, 4);

  const relatedHtml = related.length ? `
    <section class="related-section" aria-label="Related articles">
      <h2>Related Articles</h2>
      <div class="related-list">
        ${related.map(r => `
        <a class="related-item" href="/article/${articleSlug(r)}">
          <h3>${esc(r.title || 'Untitled')}</h3>
          <div class="related-item-meta">
            <span>${esc(r.category || 'News')}</span>
            <span class="sep" aria-hidden="true">·</span>
            <span>${esc(r.source || 'Unknown')}</span>
            <span class="sep" aria-hidden="true">·</span>
            <time datetime="${esc(isoDate(r.processed_at))}">${esc(shortDate(r.processed_at))}</time>
          </div>
        </a>`).join('')}
      </div>
    </section>` : '';

  const bodyHtml = basicMarkdownToHtml(article.summary || '');

  return `
    <nav class="breadcrumb" aria-label="Breadcrumb">
      <a href="/">TechPulse</a>
      <span class="sep" aria-hidden="true">›</span>
      <a href="/category/${slugify(article.category || 'news')}">${esc(article.category || 'News')}</a>
      <span class="sep" aria-hidden="true">›</span>
      <span class="current" aria-current="page">${esc((article.title || 'Article').slice(0, 50))}…</span>
    </nav>
    <article itemscope itemtype="https://schema.org/NewsArticle">
      <div class="article-header">
        <div class="article-category-badge"
             itemprop="articleSection">${esc(article.category || 'News')}</div>
        <h1 class="article-title" itemprop="headline">${esc(article.title || 'Untitled')}</h1>
        <address class="article-byline">
          <span>
            <span class="byline-label">By</span>
            <span itemprop="author" itemscope itemtype="https://schema.org/Person">
              <span itemprop="name">${esc(article.author || 'Unknown')}</span>
            </span>
          </span>
          <span><span class="byline-label">Source</span> ${esc(article.source || 'Unknown')}</span>
          <span>
            <span class="byline-label">Published</span>
            <time itemprop="datePublished" datetime="${esc(isoDate(article.processed_at))}">
              ${esc(shortDate(article.processed_at))}
            </time>
          </span>
        </address>
      </div>
      <div class="article-body" itemprop="articleBody">${bodyHtml}</div>
      <div class="source-box">
        <div class="source-box-meta">
          <span><strong>${esc(article.source || 'Unknown')}</strong></span>
          <span>Published by ${esc(article.author || 'Unknown')}</span>
        </div>
        <a class="read-original" href="${esc(article.url || '#')}" target="_blank" rel="noopener noreferrer">
          Read original
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="7" y1="17" x2="17" y2="7"/><polyline points="7 7 17 7 17 17"/></svg>
        </a>
      </div>
      ${relatedHtml}
    </article>`;
}

function buildListPrerender(articles, label, categorySlug = null) {
  const groups = new Map();
  for (const a of articles) {
    const key = isoDate(a.processed_at).slice(0, 10);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(a);
  }
  const sortedGroups = [...groups.entries()].sort((a, b) => b[0].localeCompare(a[0]));

  const breadcrumb = categorySlug
    ? `<nav class="breadcrumb" aria-label="Breadcrumb">
        <a href="/">TechPulse</a>
        <span class="sep" aria-hidden="true">›</span>
        <span class="current" aria-current="page">${esc(label)}</span>
       </nav>`
    : '';

  const groupsHtml = sortedGroups.map(([dateKey, arts]) => {
    const dateLabel = formatDateLabel(dateKey);
    return `
      <div class="date-group">
        <div class="date-label" aria-hidden="true">
          <span class="date-label-text">${esc(dateLabel)}</span>
          <span class="date-label-line"></span>
          <span class="date-label-count">${arts.length} article${arts.length !== 1 ? 's' : ''}</span>
        </div>
        <div class="grid" role="list">
          ${arts.map(a => `
          <article class="card" role="listitem">
            <a href="/article/${articleSlug(a)}" aria-label="${esc(a.title || 'Untitled')}">
              <span class="card-category">${esc(a.category || 'News')}</span>
              <h2 class="card-title">${esc(a.title || 'Untitled')}</h2>
              <div class="card-meta">
                <span>${esc(a.author || 'Unknown')}</span>
                <span class="sep" aria-hidden="true">·</span>
                <span>${esc(a.source || 'Unknown')}</span>
              </div>
            </a>
          </article>`).join('')}
        </div>
      </div>`;
  }).join('');

  return `
    ${breadcrumb}
    <div class="page-header">
      <h1 class="page-title">${esc(label)}</h1>
      <p class="page-subtitle">${articles.length} article${articles.length !== 1 ? 's' : ''}</p>
    </div>
    ${groupsHtml}`;
}

function formatDateLabel(isoKey) {
  const d = new Date(isoKey + 'T12:00:00Z');
  const now = new Date();
  const today     = now.toISOString().slice(0, 10);
  const yesterday = new Date(now - 86400000).toISOString().slice(0, 10);
  if (isoKey === today)     return 'Today';
  if (isoKey === yesterday) return 'Yesterday';
  return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
}

// ── JSON-LD BUILDERS ──────────────────────────────────────────────────────────

function articleSchema(article) {
  const wordCount = (article.summary || '').split(/\s+/).filter(Boolean).length;
  const slug      = articleSlug(article);
  return JSON.stringify({
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "@id": `${SITE_URL}/article/${slug}`,
    "headline": article.title || 'Untitled',
    "description": excerpt(article),
    "datePublished": isoDate(article.processed_at),
    "dateModified":  isoDate(article.processed_at),
    "wordCount": wordCount,
    "articleSection": article.category || 'Technology',
    "keywords": [article.category, article.source].filter(Boolean).join(', '),
    "isAccessibleForFree": true,
    "author": { "@type": "Person", "name": article.author || 'Unknown' },
    "publisher": {
      "@type": "Organization",
      "@id": `${SITE_URL}/#organization`,
      "name": SITE_NAME,
      "logo": { "@type": "ImageObject", "url": `${SITE_URL}/logo.png` }
    },
    "mainEntityOfPage": {
      "@type": "WebPage",
      "@id": `${SITE_URL}/article/${slug}`
    },
    "speakable": {
      "@type": "SpeakableSpecification",
      "cssSelector": [".article-title", ".article-body p:first-of-type"]
    },
    "url": article.url || ''
  }, null, 2);
}

function breadcrumbSchema(items) {
  return JSON.stringify({
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": items.map((item, i) => ({
      "@type": "ListItem",
      "position": i + 1,
      "name": item.name,
      ...(item.url ? { "item": `${SITE_URL}${item.url}` } : {})
    }))
  }, null, 2);
}

/**
 * CollectionPage schema for homepage and category pages.
 */
function collectionPageSchema(label, urlPath, articles) {
  return JSON.stringify({
    "@context": "https://schema.org",
    "@type": "CollectionPage",
    "@id": `${SITE_URL}${urlPath}`,
    "name": label,
    "url": `${SITE_URL}${urlPath}`,
    "description": `${label} — tech news summaries curated by AI, updated every 12 hours.`,
    "publisher": { "@id": `${SITE_URL}/#organization` },
    "hasPart": articles.slice(0, 10).map(a => ({
      "@type": "NewsArticle",
      "@id": `${SITE_URL}/article/${articleSlug(a)}`,
      "headline": a.title || 'Untitled',
      "url": `${SITE_URL}/article/${articleSlug(a)}`
    }))
  }, null, 2);
}

// ── SITEMAP ───────────────────────────────────────────────────────────────────

function buildSitemap(articles) {
  const categories = [...new Set(articles.map(a => a.category).filter(Boolean))];
  const now = new Date().toISOString().slice(0, 10);

  const staticUrls = [
    { loc: '/',       lastmod: now, priority: '1.0', changefreq: 'hourly'  },
    { loc: '/about',  lastmod: now, priority: '0.3', changefreq: 'monthly' },
  ];

  const categoryUrls = categories.map(cat => ({
    loc: `/category/${slugify(cat)}`,
    lastmod: now,
    priority: '0.7',
    changefreq: 'hourly'
  }));

  const articleUrls = articles.map(a => ({
    loc: `/article/${articleSlug(a)}`,
    lastmod: isoDate(a.processed_at).slice(0, 10),
    priority: '0.8',
    changefreq: 'monthly'
  }));

  const allUrls = [...staticUrls, ...categoryUrls, ...articleUrls];

  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${allUrls.map(u => `  <url>
    <loc>${xmlEsc(SITE_URL + u.loc)}</loc>
    <lastmod>${u.lastmod}</lastmod>
    <changefreq>${u.changefreq}</changefreq>
    <priority>${u.priority}</priority>
  </url>`).join('\n')}
</urlset>`;
}

// ── GOOGLE NEWS SITEMAP ───────────────────────────────────────────────────────

function buildNewsSitemap(articles) {
  const twoDaysAgo = Date.now() - 2 * 24 * 60 * 60 * 1000;
  const recent = articles.filter(a => {
    const d = new Date(a.processed_at);
    return !isNaN(d) && d.getTime() >= twoDaysAgo;
  });

  if (!recent.length) {
    return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
</urlset>`;
  }

  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
${recent.map(a => `  <url>
    <loc>${xmlEsc(SITE_URL)}/article/${xmlEsc(articleSlug(a))}</loc>
    <news:news>
      <news:publication>
        <news:name>${xmlEsc(SITE_NAME)}</news:name>
        <news:language>en</news:language>
      </news:publication>
      <news:publication_date>${isoDate(a.processed_at)}</news:publication_date>
      <news:title>${xmlEsc(a.title || 'Untitled')}</news:title>
      <news:keywords>${xmlEsc([a.category, a.source].filter(Boolean).join(', '))}</news:keywords>
    </news:news>
  </url>`).join('\n')}
</urlset>`;
}

// ── RSS FEED ──────────────────────────────────────────────────────────────────

function buildRssFeed(articles) {
  const recent = articles.slice(0, 50);

  const items = recent.map(a => `  <item>
    <title>${xmlEsc(a.title || 'Untitled')}</title>
    <link>${xmlEsc(SITE_URL)}/article/${xmlEsc(articleSlug(a))}</link>
    <guid isPermaLink="true">${xmlEsc(SITE_URL)}/article/${xmlEsc(articleSlug(a))}</guid>
    <pubDate>${rfcDate(a.processed_at)}</pubDate>
    <dc:creator>${xmlEsc(a.author || 'TechPulse')}</dc:creator>
    <category>${xmlEsc(a.category || 'Technology')}</category>
    <description><![CDATA[${stripMarkdown(a.summary || '').slice(0, 500)}]]></description>
    <content:encoded><![CDATA[${basicMarkdownToHtml(a.summary || '')}
<p><a href="${xmlEsc(SITE_URL)}/article/${xmlEsc(articleSlug(a))}">Read on TechPulse</a> · <a href="${xmlEsc(a.url || '#')}">Original source: ${xmlEsc(a.source || 'Unknown')}</a></p>]]></content:encoded>
  </item>`).join('\n');

  return `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:atom="http://www.w3.org/2005/Atom"
  xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>${xmlEsc(SITE_NAME)}</title>
    <link>${SITE_URL}</link>
    <description>${xmlEsc(DEFAULT_DESC)}</description>
    <language>en-us</language>
    <lastBuildDate>${rfcDate()}</lastBuildDate>
    <atom:link href="${SITE_URL}/feed.xml" rel="self" type="application/rss+xml" />
    <image>
      <url>${SITE_URL}/logo.png</url>
      <title>${xmlEsc(SITE_NAME)}</title>
      <link>${SITE_URL}</link>
    </image>
${items}
  </channel>
</rss>`;
}

// ── ROBOTS.TXT ────────────────────────────────────────────────────────────────

function buildRobotsTxt() {
  return `# TechPulse — robots.txt
User-agent: *
Allow: /

Sitemap: ${SITE_URL}/sitemap.xml
Sitemap: ${SITE_URL}/news-sitemap.xml
`;
}

// ── MAIN BUILD ────────────────────────────────────────────────────────────────

async function build() {
  console.log(`\n🔨 TechPulse Build\n   articles: ${ARTICLES_FILE}\n   output:   ${OUT_DIR}\n`);

  fs.mkdirSync(OUT_DIR, { recursive: true });

  console.log('📦 Assets:');
  extractAssetsFromIndex();

  if (!fs.existsSync(ARTICLES_FILE)) {
    console.error(`❌ articles.json not found at: ${ARTICLES_FILE}`);
    console.error('   Run your article pipeline first, then re-run build.js');
    process.exit(1);
  }

  const articles = JSON.parse(fs.readFileSync(ARTICLES_FILE, 'utf8'));
  console.log(`   Found ${articles.length} articles\n`);

  const categories = [...new Set(articles.map(a => a.category).filter(Boolean))].sort();

  // Copy articles.json into dist/ so the SPA can fetch it
  fs.copyFileSync(ARTICLES_FILE, path.join(OUT_DIR, 'articles.json'));
  console.log('  ✓ articles.json (copied to dist/)');

  // ── Homepage ────────────────────────────────────────────────────────────────
  console.log('\n📄 Pages:');
  write(
    path.join(OUT_DIR, 'index.html'),
    pageShell({
      title:            `${SITE_NAME} — AI & Technology News`,
      description:      DEFAULT_DESC,
      canonicalPath:    '/',
      breadcrumbSchema: breadcrumbSchema([{ name: SITE_NAME, url: '/' }]),
      collectionSchema: collectionPageSchema('Latest News', '/', articles),
      preRenderedContent: buildListPrerender(articles, 'Latest News')
    })
  );

  // ── About page ───────────────────────────────────────────────────────────────
  write(
    path.join(OUT_DIR, 'about.html'),
    pageShell({
      title:            `About — ${SITE_NAME}`,
      description:      `Learn about TechPulse, the fully automated AI-curated tech news feed summarizing the latest technology stories every 12 hours.`,
      canonicalPath:    '/about',
      breadcrumbSchema: breadcrumbSchema([{ name: SITE_NAME, url: '/' }, { name: 'About' }]),
      preRenderedContent: ''
    })
  );

  // ── 404 page ─────────────────────────────────────────────────────────────────
  write(
    path.join(OUT_DIR, '404.html'),
    pageShell({
      title:            `Page Not Found — ${SITE_NAME}`,
      description:      'The page or article you were looking for could not be found.',
      canonicalPath:    '/404',
      preRenderedContent: `
        <div style="text-align:center;padding:80px 24px;">
          <div style="font-size:96px;font-weight:900;opacity:0.3;font-family:Georgia,serif">404</div>
          <h1 style="font-size:28px;font-weight:700;margin:16px 0">Page not found</h1>
          <p style="color:#888;margin-bottom:24px">The article or page you're looking for doesn't exist.</p>
          <a href="/" style="display:inline-block;padding:10px 20px;border:1px solid currentColor;border-radius:8px">← Back to latest news</a>
        </div>`
    })
  );

  // ── Category pages ──────────────────────────────────────────────────────────
  console.log(`\n📂 Categories (${categories.length}):`);
  for (const cat of categories) {
    const catArticles = articles.filter(a => a.category === cat);
    const catSlug     = slugify(cat);
    const catPath     = `/category/${catSlug}`;
    const desc        = `${cat} news and summaries from TechPulse — ${catArticles.length} article${catArticles.length !== 1 ? 's' : ''} curated by AI.`;
    write(
      path.join(OUT_DIR, 'category', `${catSlug}.html`),
      pageShell({
        title:            `${cat} — ${SITE_NAME}`,
        description:      desc,
        canonicalPath:    catPath,
        breadcrumbSchema: breadcrumbSchema([
          { name: SITE_NAME, url: '/' },
          { name: cat, url: catPath }
        ]),
        collectionSchema: collectionPageSchema(cat, catPath, catArticles),
        preRenderedContent: buildListPrerender(catArticles, cat, catSlug)
      })
    );
  }

  // ── Article pages ───────────────────────────────────────────────────────────
  console.log(`\n📰 Articles (${articles.length}):`);
  let written = 0;
  for (const article of articles) {
    const slug    = articleSlug(article);
    const artPath = `/article/${slug}`;
    const catSlug = slugify(article.category || 'news');
    const desc    = excerpt(article);
    const pubTime = isoDate(article.processed_at);

    write(
      path.join(OUT_DIR, 'article', `${slug}.html`),
      pageShell({
        title:            `${article.title || 'Untitled'} — ${SITE_NAME}`,
        description:      desc,
        canonicalPath:    artPath,
        ogType:           'article',
        articleSchema:    articleSchema(article),
        breadcrumbSchema: breadcrumbSchema([
          { name: SITE_NAME, url: '/' },
          { name: article.category || 'News', url: `/category/${catSlug}` },
          { name: article.title || 'Article', url: artPath }
        ]),
        articleMeta: {
          author:       article.author   || 'Unknown',
          publishedTime: pubTime,
          modifiedTime:  pubTime,
          section:      article.category || 'Technology',
          newsKeywords: [article.category, article.source].filter(Boolean).join(', '),
          tags:         [article.category, article.source].filter(Boolean)
        },
        preRenderedContent: buildArticlePrerender(article, articles)
      })
    );

    written++;
    if (written % 25 === 0) console.log(`     … ${written} / ${articles.length}`);
  }

  // ── Sitemap ─────────────────────────────────────────────────────────────────
  console.log('\n🗺️  Infrastructure:');
  write(path.join(OUT_DIR, 'sitemap.xml'),      buildSitemap(articles));
  write(path.join(OUT_DIR, 'news-sitemap.xml'), buildNewsSitemap(articles));
  write(path.join(OUT_DIR, 'feed.xml'),         buildRssFeed(articles));
  write(path.join(OUT_DIR, 'robots.txt'),       buildRobotsTxt());

  // ── _redirects (Cloudflare Pages SPA fallback + pretty URLs) ────────────────
  const redirectsSrc = path.join(__dirname, '_redirects');
  if (fs.existsSync(redirectsSrc)) {
    fs.copyFileSync(redirectsSrc, path.join(OUT_DIR, '_redirects'));
    console.log('  ✓ _redirects (copied from root)');
  } else {
    write(
      path.join(OUT_DIR, '_redirects'),
      `/article/*   /article/:splat.html  200\n` +
      `/category/*  /category/:splat.html 200\n` +
      `/about       /about.html           200\n` +
      `/*           /index.html           200\n`
    );
  }

  // ── _headers (Cloudflare Pages response headers) ─────────────────────────────
  const headersSrc = path.join(__dirname, '_headers');
  if (fs.existsSync(headersSrc)) {
    fs.copyFileSync(headersSrc, path.join(OUT_DIR, '_headers'));
    console.log('  ✓ _headers (copied from root)');
  }

  // ── Summary ──────────────────────────────────────────────────────────────────
  const recentCount = articles.filter(a => {
    const d = new Date(a.processed_at);
    return !isNaN(d) && Date.now() - d.getTime() < 2 * 24 * 60 * 60 * 1000;
  }).length;

  console.log(`
✅ Build complete
   ${articles.length} article pages
   ${categories.length} category pages
   articles.json    — copied to dist/ for client-side fetch
   sitemap.xml      — ${articles.length + categories.length + 2} URLs
   news-sitemap.xml — ${recentCount} recent article${recentCount !== 1 ? 's' : ''} (< 2 days old)
   feed.xml         — ${Math.min(articles.length, 50)} items (with content:encoded)
   robots.txt       — references both sitemaps
   _redirects       — Cloudflare Pages routing rules

📋 Deploy checklist:
   1. Cloudflare Pages build output directory: dist
   2. Build command: node build.js
   3. GitHub secrets: CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID
   4. Make sure pipeline.yaml commits articles.json back to the repo
      before Cloudflare triggers its build
   5. After first deploy, submit BOTH sitemaps to Google Search Console:
      • https://techpulse.example.com/sitemap.xml
      • https://techpulse.example.com/news-sitemap.xml

💡 Local testing tip:
   Python's http.server doesn't support clean URL rewriting.
   For accurate local testing, use: npx serve dist
   (This respects the _redirects file via serve's SPA mode)
`);
}

build().catch(err => {
  console.error('Build failed:', err);
  process.exit(1);
});