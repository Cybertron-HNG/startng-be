# Contributing to startng-be

These rules are official. Every contributor, and every AI a contributor uses, follows them. A PR that breaks them is sent back, however well it works.

The website has its own rules, including the look of the app: see `CONTRIBUTING.md` in `startng-fe`. This file covers the API, and the part both repos share: the pages search engines read.

## Pages that search engines read: Google and people see the same page

Some pages are written twice. This API writes them as plain HTML for Google, AI tools and link previews (`app/routers/pages.py`; nginx in `startng-fe` puts them in place), and then React draws the real page. Google compares the two. If what it is served differs from what people see, it treats that as cloaking and ranks the whole site lower, or drops it. So the two versions must match.

Today these pages are written here:

| Address | Route in `app/routers/pages.py` | React page in `startng-fe` |
| --- | --- | --- |
| `/` | `GET /pages/home` | home view in `src/App.tsx`, text in `src/home-copy.ts` |
| `/games`, `/games?page=2` | `GET /pages/games` | games view in `src/App.tsx` |
| `/games/<name>` | `GET /pages/games/{name}` | `src/GamePage.tsx` |
| `/creators/<id>-<name>` | `GET /pages/creators/{id}` | `src/IndiePage.tsx` |
| `/about` | not here: written at build time in `startng-fe` | `src/AboutView.tsx` |
| `/privacy` | not here: written at build time in `startng-fe` | `src/LegalView.tsx` |
| `/terms` | not here: written at build time in `startng-fe` | `src/LegalView.tsx` |

The last three are written by `startng-fe`'s build rather than by this API, so their words live in that repo only. What this API still owns for them is the footer link (`app/server_html.py`, `site_footer`) and the sitemap entry (`FIXED_PAGES`): keep both in step.

### The content must match
1. **Nothing for Google only.** Every word, heading, link and number written here must be on the React page when it loads, with no sign-in and no clicks beyond opening a dropdown. No hidden text, no extra keywords.
2. **Nothing missing either.** The main content people see (headings, the games listed, the About and questions) must be written here too.
3. **Same words.** Copy is kept identical in both repos (for example `HOME_ABOUT` and `HOME_QUESTIONS` here, `src/home-copy.ts` in `startng-fe`). Change one, change the other in the same pair of PRs.
4. **Same data, same order, same counts.** Use the same query as the endpoint React reads: hidden games left out, the Hangout handled the same way, the same sort. Use the same numbers (games per page, games on the homepage, games in the A to Z list). Each of these constants has a comment naming its twin in `startng-fe`. Change both.
5. **Same links.** The same addresses in the same places, as real `<a href>` links.
6. **Same structure.** One `h1` with the same text, the same heading levels and the same class names as the React page.
7. **Everyone gets the same page.** Never change what is written based on the user agent, bot detection or country.
8. **A page that doesn't exist is a 404,** here and in React (an unknown game, a page of games past the last one). Never a copy of another page.

### The head must match
9. The title, description, canonical link, robots tag and preview image written here are the same as what React sets with `setSeo()` (`src/seo.ts` in `startng-fe`).
10. **Each address is its own canonical**, including pages of a list. Searches, filters, invites and private pages are `noindex`.
11. **Structured data (JSON-LD) only describes what is on the page.** No ratings, prices or authors that people can't see.
12. **The sitemap (`GET /pages/sitemap.xml`) lists only canonical, indexable addresses.** Never a `noindex` page, never a hidden game.

### Safe and quick
13. Everything that comes from a person or the database goes through `_esc()`. Never put raw names, descriptions or titles into the HTML.
14. These routes are public and called on every page view: no sign-in, one or two queries, and a short `Cache-Control` like the routes already there.

### A new page that search engines should read
It needs all of these, in one pair of PRs: the route in `pages.py` with tests that check its text (see `tests/test_pages.py`), a `location` in `nginx.conf`, a path in `src/main.tsx`, a title and description in `src/seo.ts`, an entry in the sitemap, and a row in the tables here and in `startng-fe`'s `CONTRIBUTING.md`. Never change a published address without a redirect.

### Check it before you open the PR
Run this API and the website in nginx (the `startng-fe` `Dockerfile`, with `SSR_UPSTREAM` set to this API). Then compare the two versions of each page you touched:
```bash
curl -s http://localhost:8080/games
```
That is what Google is served. Open the same address in a browser and check that every heading, game, count and link in the curl output is on the page, in the same order, and that the title, canonical and robots tags match. Say in the PR that you did this, and link the matching `startng-fe` PR.

A PR that lets the two versions drift apart is sent back, even if both look fine on their own.

## Building a feature

New features go in `app/features/<feature>/`, with one owner and one flag. `README.md` ("Building a feature") has the steps. Keep shared files (`app/main.py`, `app/models.py`, `app/routers/`) out of a feature PR unless the PR is about them.

## Before you open a pull request

1. Branch from `dev` and keep the PR to one feature. Never push to `main` or `dev` directly.
2. Run the tests: `python -m pytest -q`. CI runs them too. Add tests for what you changed.
3. A change to the database needs an alembic migration. Renumber it right before merging if another one landed first.
4. Never commit secrets. New settings go in `app/config.py` with a safe default and in `.env.example`.

## What CI checks

- **Tests** (`tests.yml`): `pytest`.
- **Malware Scan (CI Gate)**: blocks known malicious code patterns.
- **CodeRabbit**: automatic review.

## If you use an AI

Give it this file first, and `startng-fe`'s `CONTRIBUTING.md` if the change touches a page search engines read. Check what it gives you against the rules above before you open the PR. The PR is yours, not the AI's.
