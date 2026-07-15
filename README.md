# Medium AI Reader Daily Digest

A Firebase-ready scheduled digest that explores public Medium RSS feeds, ranks the articles that best match a reading intent, and emails a daily digest to `vishnucheppanam@gmail.com`.

## What the cron job does

The job uses an agent-style pipeline:

1. **PreferenceAgent** turns a fuzzy prompt like "I want practical AI agent engineering posts, not hype" into Medium tags and source feeds.
2. **ExplorerAgent** builds supported Medium RSS URLs for tags, profiles, publications, publication-tag pages, and custom-domain publications.
3. **FetcherAgent** reads public RSS entries with a respectful user agent and light pacing.
4. Optionally, **PopularityAgent** visits public article pages to extract claps, response counts, and reading time when Medium embeds those fields.
5. **RankerAgent** scores each article against the user's intent.
   - With `OPENROUTER_API_KEY`, it uses embeddings.
   - Without a key, it falls back to local TF-IDF ranking.
6. **CuratorAgent** explains why each article is worth reading.
7. **Mailer** sends the digest through SMTP.
8. **DeliveryHistory** records sent article keys in Firestore so future scheduled runs do not resend them.

## Data source and boundaries

This MVP uses public RSS feeds. It does **not** bypass Medium membership, login, paywalls, robots rules, or private content. Paywalled Medium stories may appear only as truncated RSS previews.

Medium RSS does not include clap counts. The optional popularity filter makes one public page request per unique article and extracts claps, response counts, and reading time from embedded page metadata when available.

Supported feed shapes include:

```text
https://medium.com/feed/@username
https://medium.com/feed/publication-name
https://medium.com/feed/tag/tag-name
https://medium.com/feed/publication-name/tagged/tag-name
https://custom-domain-publication.com/feed
```

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py --dry-run
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python app.py --dry-run
```

Set SMTP values in `.env`, then run the real email job:

```bash
python app.py
```

For local real sends, use Firebase credentials that can access Firestore or explicitly set `DIGEST_REQUIRE_DELIVERY_HISTORY=false`.

## Firebase Hosting setup

This repository includes Firebase configuration for Hosting, Firestore, and a scheduled Python Cloud Function:

```text
firebase.json
firestore.rules
main.py
public/
```

The default schedule is `0 13 * * *` in UTC. Firebase Hosting serves the static page in `public/`; the digest job itself runs as the `daily_digest` scheduled function and stores delivery history in Firestore.

For the full deployment walkthrough, see [FIREBASE_HOSTING.md](FIREBASE_HOSTING.md).

## Configuration

The main environment variables are:

```text
DIGEST_RECIPIENTS=vishnucheppanam@gmail.com
DIGEST_INTENT=Practical, non-hype articles about building useful AI agents with Python and product thinking.
DIGEST_TAGS=artificial-intelligence, ai-agents, python, software-development
DIGEST_SOURCES=
DIGEST_TOP_K=8
DIGEST_REQUIRE_DELIVERY_HISTORY=true
DIGEST_HISTORY_COLLECTION=sent_articles
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM=
```

`DIGEST_SOURCES` can include Medium profiles, publications, or custom-domain publication URLs separated by commas or newlines.

## Optional AI setup

The cron job works without an API key. To enable embedding-based matching and AI-generated curation notes, set this in `.env`:

```bash
OPENROUTER_API_KEY=your_key_here
OPENROUTER_EMBEDDING_MODEL=tencent/hy3:free
OPENROUTER_CHAT_MODEL=tencent/hy3:free
```

You can change model names in `.env` without editing the app code.

## Example reading intents

```text
Practical articles about building AI agents with Python, tool calling, memory, evaluation, and production pitfalls.
```

```text
Deep product strategy essays for B2B SaaS founders, especially pricing, retention, and sales-led growth.
```

```text
Clear, beginner-friendly data science articles with real projects and code, not generic career advice.
```

## Next features to add

- Save thumbs-up/thumbs-down feedback and learn a user profile.
- Add a vector database such as SQLite + sqlite-vss, Chroma, or Postgres/pgvector.
- Add per-user source libraries.
- Add freshness filters, author exclusions, and "avoid hype" classifier.

## Tests

```bash
pytest
```
