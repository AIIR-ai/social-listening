import os
import json
import subprocess
import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from openai import OpenAI

# === Load Config & Setup ===
with open("config.json", "r") as f:
    config = json.load(f)

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

sender = config["sender_email"]
recipients = config["receiver_emails"]
subject = config["email_subject"]
days_back = config["days_back"]
timezone = config["timezone"]

# === Get Date Range ===
today = datetime.datetime.utcnow()
since_date = today - datetime.timedelta(days=days_back)
since_str = since_date.strftime("%-d %B")
until_str = today.strftime("%-d %B")

# === Load Keywords ===
with open("keywords.txt", "r") as f:
    keywords = [line.strip() for line in f if line.strip()]

# === Helper: Scrape Tweets ===
def scrape_tweets(keyword):
    query = f'"{keyword}" since:{since_date.strftime("%Y-%m-%d")} until:{today.strftime("%Y-%m-%d")}'
    cmd = ["snscrape", "--jsonl", "twitter-search", query]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        lines = result.stdout.strip().split("\n")
        tweets = [json.loads(line) for line in lines if line]
        return tweets
    except Exception as e:
        print(f"Error scraping {keyword}: {e}")
        return []

# === Helper: Analyse Sentiment ===
def summarise_tweets(keyword, tweets):
    posts_text = "\n".join([t["content"] for t in tweets[:20]])
    prompt = f"""
Analyse the following recent posts about \"{keyword}\" from X (formerly Twitter).
Provide a concise paragraph including:
- Sentiment overview (positive, negative, neutral)
- Key discussion themes
- Optional links to relevant/high-performing posts

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
        return summary, sentiment
    except Exception as e:
        return f"Error generating summary for {keyword}: {e}", "Unknown"

# === Compose Email Content ===
email_body = f"<h2>Weekly Social Analysis</h2>"
email_body += f"<p>Analysis from {since_str} to {until_str}</p><hr>"

for keyword in keywords:
    tweets = scrape_tweets(keyword)
    summary, sentiment = summarise_tweets(keyword, tweets)
    total = len(tweets)
    search_url = f"https://twitter.com/search?q={keyword.replace(' ', '%20')}&src=typed_query"

    # Sort tweets by viewCount (if available) and extract top 3 links
    top_links = ""
    if tweets:
        sorted_tweets = sorted(tweets, key=lambda x: x.get("viewCount", 0), reverse=True)
        top_links = "<ul>"
        for tweet in sorted_tweets[:3]:
            tweet_url = f"https://twitter.com/{tweet['user']['username']}/status/{tweet['id']}"
            top_links += f"<li><a href='{tweet_url}'>Top post</a></li>"
        top_links += "</ul>"

    email_body += f"<h3>{keyword}</h3>"
    email_body += f"<p><strong>Posts analysed:</strong> {total}<br>"
    email_body += f"<strong>Sentiment:</strong> {sentiment}<br>"
    email_body += f"<strong>Search link:</strong> <a href='{search_url}'>View on Twitter</a></p>"
    email_body += f"<p>{summary}</p>"
    if top_links:
        email_body += f"<p><strong>Top posts:</strong>{top_links}</p>"
    email_body += f"<hr>"

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
