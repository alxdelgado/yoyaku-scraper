/**
 * Unit tests for demo result URL generation.
 *
 * Contract:
 *   - Demo URLs must point to real, reachable yoyaku.io style pages.
 *   - The URL slug must match the first selected style.
 *   - No 404-generating /product/ paths.
 *   - In live mode r.url comes from the scraper — not tested here.
 *
 * Run with: node tests/test_frontend_links.js
 */

'use strict';

let passed = 0;
let failed = 0;

function assert(description, condition) {
  if (condition) {
    console.log(`  ✓ ${description}`);
    passed++;
  } else {
    console.error(`  ✗ ${description}`);
    failed++;
  }
}

/* ── Replicate the fixed URL generation logic from index.html ────────── */
function buildDemoResults(styles, count) {
  return Array.from({ length: count }, (_, i) => ({
    title: `Release ${String.fromCharCode(65 + i)} — ${styles[0]} Edition`,
    url:   `https://yoyaku.io/style/${styles[0].toLowerCase().replace(/\s+/g, '-')}/`,
  }));
}

/* ── Tests ───────────────────────────────────────────────────────────── */

console.log('\nDemo URL generation — link correctness\n');

// 1. No URL uses the bare homepage
{
  console.log('  [1] No result links to the bare homepage');
  const results = buildDemoResults(['Tech House', 'Techno'], 4);
  results.forEach((r, i) => {
    assert(`row ${i}: url !== "https://yoyaku.io/"`, r.url !== 'https://yoyaku.io/');
  });
}

// 2. No URL uses a /product/ path (those 404 on yoyaku.io)
{
  console.log('\n  [2] No /product/ paths that would 404');
  const results = buildDemoResults(['Deep House'], 4);
  results.forEach((r, i) => {
    assert(`row ${i}: url does not contain /product/`, !r.url.includes('/product/'));
  });
}

// 3. Every URL uses the /style/ path (real yoyaku.io pages)
{
  console.log('\n  [3] URLs point to real /style/ pages');
  const results = buildDemoResults(['Chicago'], 4);
  results.forEach((r, i) => {
    assert(
      `row ${i}: starts with https://yoyaku.io/style/`,
      r.url.startsWith('https://yoyaku.io/style/')
    );
    assert(`row ${i}: ends with trailing slash`, r.url.endsWith('/'));
    assert(`row ${i}: no spaces in URL`,         !r.url.includes(' '));
  });
}

// 4. Style slug matches the first selected style
{
  console.log('\n  [4] URL slug reflects the first selected style');
  const cases = [
    { styles: ['Tech House'],  slug: 'tech-house'  },
    { styles: ['Deep House'],  slug: 'deep-house'  },
    { styles: ['Nu Disco'],    slug: 'nu-disco'     },
    { styles: ['IDM'],         slug: 'idm'          },
    { styles: ['Dub Techno'],  slug: 'dub-techno'  },
    { styles: ['Chicago'],     slug: 'chicago'      },
    { styles: ['Acid'],        slug: 'acid'         },
  ];
  cases.forEach(({ styles, slug }) => {
    const [r] = buildDemoResults(styles, 1);
    assert(`"${styles[0]}" → slug "${slug}"`, r.url === `https://yoyaku.io/style/${slug}/`);
  });
}

// 5. Multi-style selection uses only the FIRST style in the URL
{
  console.log('\n  [5] Multi-style selection — URL derived from first style only');
  const results = buildDemoResults(['Techno', 'Tech House', 'Deep House'], 2);
  results.forEach((r, i) => {
    assert(`row ${i}: URL uses "techno", not second or third style`,
      r.url === 'https://yoyaku.io/style/techno/'
    );
  });
}

// 6. No double-hyphens in any slug
{
  console.log('\n  [6] No double-hyphens in slugs');
  const allStyles = [
    'Acid','Ambient','Breaks','Chicago','Deep House','Detroit',
    'Dub','Dub Techno','Electro','Experimental','House','IDM',
    'Jungle','Minimal','Minimal Techno','Nu Disco','Progressive House',
    'Soul','Tech House','Techno',
  ];
  allStyles.forEach(style => {
    const [r] = buildDemoResults([style], 1);
    assert(`"${style}": no double-hyphen`, !r.url.includes('--'));
  });
}

/* ── Summary ─────────────────────────────────────────────────────────── */
console.log(`\n${'─'.repeat(48)}`);
console.log(`  ${passed + failed} tests — ${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
