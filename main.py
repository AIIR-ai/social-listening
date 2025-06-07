import os
import json
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import tweepy
import praw
import openai

load_dotenv()

# === Twitter API Setup ===
twitter_client = tweepy.Client(bearer_token=os.getenv("TWITTER_BEARER_TOKEN"))

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
print("DEBUG - config loaded:", config)
recipient_emails = config["receiver_emails"]
sender_email = config["sender_email"]
days_back = config.get("days_back", 7)

# === Date range ===
end_time = datetime.now(timezone.utc)
start_time = end_time - timedelta(days=days_back)
date_range_display = f"{start_time.strftime('%-d %B')} to {end_time.strftime('%-d %B')}"

# === Tweet scraping ===
def fetch_tweets(keyword, start_time, end_time, max_results=100):
    query = f'"{keyword}" -is:retweet lang:en'
    tweets = []
    try:
        for tweet in tweepy.Paginator(
            twitter_client.search_recent_tweets,
            query=query,
            start_time=start_time,
            end_time=end_time,
            tweet_fields=['created_at', 'public_metrics', 'text'],
            max_results=100
        ).flatten(limit=max_results):
            tweets.append({
                "text": tweet.text,
                "created_at": tweet.created_at,
                "retweet_count": tweet.public_metrics.get("retweet_count", 0),
                "like_count": tweet.public_metrics.get("like_count", 0),
                "url": f"https://twitter.com/i/web/status/{tweet.id}"
            })
    except Exception as e:
        print(f"Error fetching tweets for {keyword}: {e}")
    return tweets

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
    text_blob = "\n".join(p["text"] for p in posts if "text" in p)
    prompt = f"""Summarise the following online posts about "{keyword}" over the past 7 days.
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
            sentiment = "Positive"
        elif "negative" in summary.lower():
            sentiment = "Negative"
        return summary, sentiment
    except Exception as e:
        return f"Error generating summary for {keyword}: {e}", "Unknown"

# === Generate and send email ===
def build_email_content():
    all_blocks = []
    for keyword in keywords:
        block = f"<h2>{keyword}</h2>"
        block += f"<p><b>Analysis from:</b> {date_range_display}</p>"

        # Twitter
        tweets = fetch_tweets(keyword, start_time, end_time)
        block += f"<p><b>Twitter posts analysed:</b> {len(tweets)}</p>"
        if tweets:
            tweets_sorted = sorted(tweets, key=lambda x: x["like_count"], reverse=True)
            top_links = "".join(f'<li><a href="{t["url"]}">{t["text"][:60]}...</a></li>' for t in tweets_sorted[:3])
            twitter_summary, twitter_sentiment = summarise_posts(tweets, keyword)
            block += f"<p>{twitter_summary}</p>"
            block += f"<ul>{top_links}</ul>"
        else:
            block += "<p>No Twitter data available.</p>"
            twitter_sentiment = "Unknown"

        # Reddit
        reddit_posts = scrape_reddit(keyword)
        block += f"<p><b>Reddit posts analysed:</b> {len(reddit_posts)}</p>"
        if reddit_posts:
            reddit_summary, reddit_sentiment = summarise_posts(reddit_posts, keyword)
            top_reddit = "".join(f'<li><a href="{p["url"]}">{p["title"][:60]}...</a></li>' for p in reddit_posts[:3])
            block += f"<p>{reddit_summary}</p>"
            block += f"<ul>{top_reddit}</ul>"
        else:
            block += "<p>No Reddit data available.</p>"
            reddit_sentiment = "Unknown"

        block += f"<p><b>Overall sentiment:</b> {twitter_sentiment or reddit_sentiment}</p>"
        search_url = f"https://twitter.com/search?q={keyword.replace(' ', '%20')}&src=typed_query"
        block += f'<p><a href="{search_url}">View on Twitter</a></p>'
        all_blocks.append(block)

    return "<html><body>" + "".join(all_blocks) + "</body></html>"

def send_email(html_content):
    msg = MIMEText(html_content, "html")
    msg["Subject"] = config.get("email_subject", "Weekly Keyword Summary")
    msg["From"] = formataddr(("Keyword Bot", sender_email))
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
