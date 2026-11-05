# Event Scraper Bot

A daily bot that checks **Luma** and the **events pages of big tech and AI/data companies**
(Google, Meta, AWS, Apple, Microsoft, NVIDIA, OpenAI, Anthropic, MongoDB, Neo4j, Databricks,
Snowflake, Pinecone, Weaviate, Vercel, LangChain, and more) for:

- 🛠️ **Hackathons** (in person or online)
- 🍻 **In-person networking**: mixers, meetups, happy hours, demo nights
- 🎤 **Conferences and summits**
- 📅 Any other upcoming event listed on a company's events page

Every morning it emails you a digest of **new** events. Each event has a link, date, location and a
**two-word tag** (e.g. `AI Hackathon`, `Founder Mixer`, `Graph Meetup`). Events already sent are
recorded in `data/seen.json`, so you never get the same one twice.

## Setup (about 5 minutes)

1. **Create a Gmail App Password** for the account that will *send* the email:
   Google Account → Security → 2-Step Verification (must be on) → App passwords → create one.
2. In this GitHub repo go to **Settings → Secrets and variables → Actions → New repository secret**
   and add:

   | Secret          | Value                                                  |
   |-----------------|--------------------------------------------------------|
   | `EMAIL_TO`      | the address that should receive the digest             |
   | `SMTP_USER`     | the Gmail address that sends it                        |
   | `SMTP_PASSWORD` | the 16-character App Password from step 1              |
   | `SMTP_HOST`     | *(optional)* defaults to `smtp.gmail.com`              |
   | `SMTP_PORT`     | *(optional)* defaults to `465`                         |

3. Merge this branch into the default branch (GitHub only runs scheduled workflows from there).
4. Go to **Actions → Daily event digest → Run workflow** to send the first email right away.
   After that it runs every day at 13:17 UTC (change the `cron` line in
   `.github/workflows/daily.yml` for a different time).

## Top-5 hackathon email

By default the daily email contains only the **top 5 in-person NYC/NJ hackathons, ranked by
networking ROI for landing an internship** (sponsors/recruiters from strong employers, explicit
recruiting, student focus, size, prizes). Each has a one-line "why it's worth it". Hackathons
that weren't in a previous email are marked **NEW**.

Hackathon sources: Google (Google Events + web results), Devpost, MLH, Luma and the company pages.

Optional secrets that improve it:

| Secret              | What it does                                                                     |
|---------------------|----------------------------------------------------------------------------------|
| `SERPAPI_KEY`       | Turns on Google results (serpapi.com, free plan = 100 searches/month; bot uses ~3/day) |
| `ANTHROPIC_API_KEY` | Claude ranks the hackathons using your `ranking.profile` instead of the point score |
| `GOOGLE_API_KEY` + `GOOGLE_CSE_ID` | Alternative to SerpAPI for web results (Google Programmable Search) |

Set `email.mode: digest` in `config.yaml` to go back to the full list of mixers, meetups and conferences.

## Customizing

Everything lives in [`config.yaml`](config.yaml):

- `locations`: places to keep events for (currently NYC — Manhattan, Brooklyn, Queens — and
  New Jersey). Events elsewhere, online, or with no recognizable location are dropped. Delete
  the list to get events from everywhere.
- `luma.pages`: Luma cities (`sf`, `nyc`, `seattle`, ...), categories (`ai`, `tech`) or any Luma
  calendar slug. Add your own city here.
- `luma.search_terms`: extra Luma searches.
- `companies`: name, events page `url`, and LinkedIn slug. Add any company you like.
- `keywords`: words that make something a hackathon, networking event or conference.
- `lookahead_days`, `email_when_empty`.

## About LinkedIn

LinkedIn company event tabs only load for signed-in users, and LinkedIn's terms forbid automated
scraping of logged-in pages (accounts used for it get restricted). So the bot doesn't log in to
LinkedIn. Instead:

- any `linkedin.com/events/...` link found on a company's events page is picked up automatically, and
- every digest ends with one-click links to each company's LinkedIn events tab, so checking them
  takes a few seconds.

## Running locally

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m event_bot --dry-run        # writes out/digest.html, sends nothing
python -m event_bot --dry-run --all  # include already-emailed events
```

If a source breaks (a company redesigns its site), the email lists it under
"Sources that failed this run"; fix or remove its URL in `config.yaml`.
