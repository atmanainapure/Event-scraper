# Event Scraper Bot

Every morning at **8 AM New York time** this bot emails you:

1. **Hackathons** and **networking events** in NYC / New Jersey found on **Luma**, **Meetup** and
   **Cerebral Valley**. Networking events are kept only if they're tech or career related
   (engineers, founders, AI, recruiting, internships...), so they can help you land a tech
   internship or build a strong network.
2. Filtered to events involving the **top 8 tech / AI companies**: Google (Gemini, DeepMind),
   Amazon (AWS), Meta, Apple, Microsoft, NVIDIA, OpenAI, Anthropic (Claude).
3. If none of those is a hackathon, the bot **Googles "&lt;company&gt; hackathon New York City"**
   for each of the 8 companies and adds what it finds as **Google leads**.

Events you haven't been sent before are marked **NEW**. Edit companies, keywords, cities and
sources in [`config.yaml`](config.yaml).

## Secrets (repo Settings → Secrets and variables → Actions)

| Secret          | Needed for |
|-----------------|------------|
| `EMAIL_TO`      | where the email goes |
| `SMTP_USER`     | the Gmail address that sends it |
| `SMTP_PASSWORD` | a Gmail App Password for that address |
| `SERPAPI_KEY`   | the Google search step (serpapi.com). Each fallback day uses up to 16 searches (8 companies × Events + web results), so the free 100/month plan covers about 6 fallback days. |

`GOOGLE_API_KEY` + `GOOGLE_CSE_ID` (Google Programmable Search) work instead of SerpAPI for web results.

## Timing

GitHub's built-in schedule often starts hours late, so a scheduled task presses **Run workflow**
at 7:54 AM New York time and the email lands around 8:00. The workflow's own cron (~9:37 AM ET)
is a backup that does nothing if the morning email already went out.

## Checking a missed event

Actions → Daily event digest → Run workflow → paste the event link into **check_url**. Nothing is
emailed; the run's log says what the bot read from the page and why it would keep or drop it.

## Running locally

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m event_bot --dry-run   # writes out/digest.html, sends nothing
```
