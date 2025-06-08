import os
import json
import smtplib
import subprocess
import time
from email.mime.text import MIMEText
from email.utils import formataddr
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import praw
import openai

load_dotenv()

# === Reddit API Setup ===
reddit = praw.Reddit(
    client_id=os.getenv("REDDIT_CLIENT_ID"),
    client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
    user_agent="keyword-sentiment-tracker"
)

# === Load keyword list ===
with open("keywords.txt", "r") as f:
    keywords = [line.strip() for line in f if line.strip()]

# === Load config ===
with open("config.json", "r") as f:
    config = json.load(f)

recipient_emails = config["receiver_emails"]
sender_email = config["sender_email"]
days_back = config.get("days_back", 7)

# === Date range ===
end_time = datetime.now(timezone.utc)
start_time = end_time - timedelta(days=days_back)
date_range_display = f"Week of {start_time.strftime('%A, %d %B %Y')} to {end_time.strftime('%A, %d %B %Y')}"

# === Twitter (snscrape) ===
def run_snscrape(keyword, max_retries=3, delay=5):
    query = f'{keyword} since:{start_time.date()} until:{end_time.date()}'
    print(f"[snscrape] Running query: {query}")

    for attempt in range(max_retries):
        try:
            result = subprocess.run(
                ["snscrape", "--jsonl", "twitter-search", query],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                print(f"[snscrape] Error (attempt {attempt + 1}): {result.stderr.strip()}")
            else:
                lines = result.stdout.strip().splitlines()
                print(f"[snscrape] Attempt {attempt + 1} returned {len(lines)} lines")
                if lines:
                    tweets = [json.loads(line) for line in lines]
                    return tweets
                else:
                    print("[snscrape] No results returned.")
        except Exception as e:
            print(f"[snscrape] Exception on attempt {attempt + 1}: {e}")
        time.sleep(delay)

    print(f"[snscrape] All attempts failed for query: {query}")
    with open("failed_queries.log", "a") as log:
        log.write(f"{time.ctime()}: {query}\n")
    return []

# === Reddit scraping ===
def scrape_reddit(keyword, limit=20):
    results = []
    for submission in reddit.subreddit("all").search(keyword, sort="relevance", time_filter="week", limit=limit):
        if keyword.lower() in submission.title.lower() or keyword.lower() in submission.selftext.lower():
            results.append({
                "title": submission.title,
                "text": submission.selftext,
                "url": submission.url,
                "score": submission.score,
                "num_comments": submission.num_comments
            })
    return results

# === Summarisation ===
openai.api_key = os.getenv("OPENAI_API_KEY")

def summarise_posts(posts, keyword):
    text_blob = "\n".join(p.get("text", p.get("title", "")) for p in posts)
    prompt = f"""Summarise the following online posts about \"{keyword}\" over the past 7 days.
Give a sentiment overview, key talking points, and briefly highlight themes.

{text_blob[:3000]}"""

    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=300
        )
        summary = response.choices[0].message.content.strip()
        sentiment = "Neutral"
        if "positive" in summary.lower():
            sentiment = "<b style='color:green;'>Positive</b>"
        elif "negative" in summary.lower():
            sentiment = "<b style='color:red;'>Negative</b>"
        return f"<p>{summary}</p>", sentiment
    except Exception as e:
        return f"<p>Error generating summary for {keyword}: {e}</p>", "Unknown"

# === Generate and send email ===
def build_email_content():
    x_logo = "https://upload.wikimedia.org/wikipedia/commons/9/95/Twitter_new_X_logo.png"
    reddit_logo = "https://redditinc.com/hs-fs/hubfs/Reddit%20Inc/Content/Brand%20Page/Reddit_Logo.png?width=800&height=800&name=Reddit_Logo.png"

    all_blocks = []
    for keyword in keywords:
        block = f"""
        <div style='border:1px solid #ccc; padding:15px; margin:20px 0;'>
            <h2>{keyword}</h2>
            <p><b>Week:</b> {date_range_display}</p>
        """

        # X
        tweets = run_snscrape(keyword)
        block += f"<h3><img src='{x_logo}' alt='X logo' width='20' style='vertical-align:middle;'> X Posts Analysed: {len(tweets)}</h3>"
        if tweets:
            tweets_sorted = sorted(tweets, key=lambda x: x.get("likeCount", 0), reverse=True)
            twitter_summary, twitter_sentiment = summarise_posts(tweets, keyword)
            block += f"<p><b>Sentiment (X):</b> {twitter_sentiment}</p>"
            block += twitter_summary
            block += "<ul>" + "".join(
                f"<li><a href='{t['url']}'>{t['content'][:80]}...</a></li>"
                for t in tweets_sorted[:3]) + "</ul>"
        else:
            block += "<p>No X data available.</p>"

        # Reddit
        reddit_posts = scrape_reddit(keyword)
        block += f"<h3><img src='{reddit_logo}' alt='Reddit logo' width='20' style='vertical-align:middle;'> Reddit Posts Analysed: {len(reddit_posts)}</h3>"
        if reddit_posts:
            reddit_summary, reddit_sentiment = summarise_posts(reddit_posts, keyword)
            block += f"<p><b>Sentiment (Reddit):</b> {reddit_sentiment}</p>"
            block += reddit_summary
            block += "<ul>" + "".join(
                f"<li><a href='{p['url']}'>{p['title'][:80]}...</a></li>"
                for p in reddit_posts[:3]) + "</ul>"
        else:
            block += "<p>No Reddit data available.</p>"

        block += f"<p><a href='https://twitter.com/search?q={keyword.replace(' ', '%20')}&src=typed_query'>Search this keyword on X</a></p>"
        block += "</div>"
        all_blocks.append(block)

    return "<html><body>" + "".join(all_blocks) + "</body></html>"

def send_email(html_content):
    subject_date = start_time.strftime("w/c %d %B %Y")
    subject_line = f"AIIR Weekly Social Listening – {subject_date}"

    msg = MIMEText(html_content, "html")
    msg["Subject"] = subject_line
    msg["From"] = formataddr(("AIIR Weekly Social Listening", sender_email))
    msg["To"] = ", ".join(recipient_emails)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender_email, os.getenv("EMAIL_APP_PASSWORD"))
            server.sendmail(sender_email, recipient_emails, msg.as_string())
        print("✅ Email sent.")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

# === Run everything ===
if __name__ == "__main__":
    email_content = build_email_content()
    send_email(email_content)
