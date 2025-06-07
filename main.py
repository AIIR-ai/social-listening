import os
import openai
import subprocess
import datetime
import json
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import praw
print("DEBUG - REDDIT_CLIENT_ID found:", "REDDIT_CLIENT_ID" in os.environ)

# === OpenAI & Reddit Client Setup ===
from openai import OpenAI
client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

reddit = praw.Reddit(
    client_id=os.environ["REDDIT_CLIENT_ID"],
    client_secret=os.environ["REDDIT_CLIENT_SECRET"],
    user_agent="social-listening-script"
)

# === Load Config & Keywords ===
with open("config.json", "r") as f:
    config = json.load(f)

sender = config["sender_email"]
recipients = config["receiver_emails"]
subject = config["email_subject"]
days_back = config["days_back"]
timezone = config["timezone"]

with open("keywords.txt", "r") as f:
    keywords = [line.strip() for line in f if line.strip()]

# === Date Range Formatting ===
today = datetime.datetime.utcnow()
since_date = today - datetime.timedelta(days=days_back)
since_str = since_date.strftime("%Y-%m-%d")
until_str = (today + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
since_nice = since_date.strftime("%-d %B")
until_nice = today.strftime("%-d %B")

# === Scrape Tweets with snscrape ===
def scrape_tweets(keyword):
clean_keyword = f'"{keyword}"' if " " in keyword else keyword
query = f'{clean_keyword} since:{since_str} until:{until_str}'
    print("SCRAPE DEBUG:", "snscrape", "--jsonl", "twitter-search", query)
    cmd = ["snscrape", "--jsonl", "twitter-search", query]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        lines = result.stdout.strip().split("\n")
        tweets = [json.loads(line) for line in lines if line]
        return tweets
    except Exception as e:
        print(f"Error scraping {keyword}: {e}")
        return []

# === Scrape Reddit Posts ===
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


# === Generate Email Body ===
email_body = f"<h2>Weekly Keyword Summary</h2>"
email_body += f"<p>Analysis from {since_nice} to {until_nice}</p><hr>"

for keyword in keywords:
    email_body += f"<h3>{keyword}</h3>"

    # === Twitter Section ===
    tweets = scrape_tweets(keyword)
    posts_text = "\n".join([t["content"] for t in tweets[:20]])
    summary = "No data available."
    sentiment = "Unknown"

    if tweets:
        prompt = f"""
Analyse the following recent posts about "{keyword}" from X (formerly Twitter).
Provide a concise paragraph including:
- Sentiment overview (positive, negative, neutral)
- Key discussion themes
- Include links to 2–3 high-engagement posts if relevant

Posts:
{posts_text}

Summarise clearly and professionally:
"""
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=300
            )
            summary = response.choices[0].message.content.strip()
            if "positive" in summary.lower():
                sentiment = "Positive"
            elif "negative" in summary.lower():
                sentiment = "Negative"
            else:
                sentiment = "Neutral"
        except Exception as e:
            summary = f"Error generating summary for {keyword}: {e}"

    tweet_links = sorted(tweets, key=lambda t: t.get("viewCount", t.get("retweetCount", 0)), reverse=True)[:3]
    top_links_html = "<br>".join([f"<a href='{t['url']}'>Tweet Link</a>" for t in tweet_links])

    email_body += f"<p><strong>Posts analysed:</strong> {len(tweets)}<br>"
    email_body += f"<strong>Sentiment:</strong> {sentiment}<br>"
    email_body += f"<strong>Search link:</strong> <a href='https://twitter.com/search?q={keyword.replace(' ', '%20')}&src=typed_query'>View on Twitter</a></p>"
    email_body += f"<p>{summary}</p>"
    if tweet_links:
        email_body += f"<p><strong>Top Tweets:</strong><br>{top_links_html}</p>"
    email_body += "<hr>"

    # === Reddit Section ===
    reddit_posts = scrape_reddit(keyword)
    if reddit_posts:
        reddit_text = "\n\n".join([f"{post['title']} - {post['text']}" for post in reddit_posts[:10]])
        reddit_prompt = f"""
Analyse the following Reddit posts discussing "{keyword}".

Provide a short paragraph summary including:
- General sentiment
- Key discussion themes
- Include 2–3 of the most upvoted links if relevant

Reddit posts:
{reddit_text}
"""
        try:
            reddit_summary = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": reddit_prompt}],
                temperature=0.7,
                max_tokens=300
            ).choices[0].message.content.strip()
        except Exception as e:
            reddit_summary = f"Error generating Reddit summary: {e}"

        reddit_links = sorted(reddit_posts, key=lambda p: p["score"], reverse=True)[:3]
        reddit_links_html = "<br>".join([f"<a href='{p['url']}'>Reddit Post</a>" for p in reddit_links])

        email_body += f"<h4>Reddit Summary</h4><p>{reddit_summary}</p>"
        email_body += f"<p><strong>Top Reddit Posts:</strong><br>{reddit_links_html}</p><hr>"

# === Send Email ===
def send_email(subject, body, sender, recipients):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, os.environ["EMAIL_APP_PASSWORD"])
            server.sendmail(sender, recipients, msg.as_string())
        print("✅ Email sent.")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")

send_email(subject, email_body, sender, recipients)
