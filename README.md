# Medium AI Reader Finder

A local Streamlit MVP that explores public Medium RSS feeds and recommends the articles that best match a user's reading intent.

## What the app does

The app uses an agent-style pipeline:

1. **PreferenceAgent** turns a fuzzy prompt like "I want practical AI agent engineering posts, not hype" into Medium tags and source feeds.
2. **ExplorerAgent** builds supported Medium RSS URLs for tags, profiles, publications, publication-tag pages, and custom-domain publications.
3. **FetcherAgent** reads public RSS entries with a respectful user agent and light pacing.
4. **RankerAgent** scores each article against the user's intent.
   - With `OPENAI_API_KEY`, it uses embeddings.
   - Without a key, it falls back to local TF-IDF ranking.
5. **CuratorAgent** explains why each article is worth reading.

## Data source and boundaries

This MVP uses public RSS feeds. It does **not** bypass Medium membership, login, paywalls, robots rules, or private content. Paywalled Medium stories may appear only as truncated RSS previews.

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
cd medium-ai-reader
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

On Windows PowerShell:

```powershell
cd medium-ai-reader
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
streamlit run app.py
```

## Optional AI setup

The app works without an API key. To enable embedding-based matching and AI-generated curation notes, set this in `.env`:

```bash
OPENAI_API_KEY=your_key_here
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_CHAT_MODEL=gpt-5.4-mini
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
- Add scheduled digest emails.
- Add a vector database such as SQLite + sqlite-vss, Chroma, or Postgres/pgvector.
- Add per-user source libraries.
- Add freshness filters, reading-time filters, author exclusions, and "avoid hype" classifier.
- Add deployment through Streamlit Community Cloud, Render, Fly.io, or a Docker container.

## Tests

```bash
PYTHONPATH=src pytest
```
