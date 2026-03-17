import os
import requests
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

# --- CONFIGURATION ---
BASE_URL = "https://newsapi.org/v2/everything"
TOPIC = "Iphone"
DOMAINS = ""
PAGE_SIZE = 5
SORT_BY = "relevancy"

def fetch_popular_news(topic, domains):
    # Load the .env file and grab the key
    load_dotenv()
    api_key = os.getenv("NEWS_API_KEY")
    
    if not api_key:
        print("Error: NEWS_API_KEY not found in .env file")
        return

    # Calculate the 12-hour window (UTC)
    now = datetime.utcnow()
    from_date = (now - timedelta(hours=37)).isoformat()
    to_date = (now - timedelta(hours=25)).isoformat()

    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
    params = {
        "q": topic,
        "from": from_date,
        "to": to_date,
        "language": "en",
        "domains": domains,
        "pageSize": PAGE_SIZE,
        "sortBy": SORT_BY,
        "apiKey": api_key
    }
    # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

    try:
        response = requests.get(BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"news_{timestamp}.json"

        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        
        print(f"Successfully saved news to {filename}")
        print(f"Window: {from_date} to {to_date}")

    except requests.exceptions.RequestException as e:
        print(f"Error fetching news: {e}")

if __name__ == "__main__":
    fetch_popular_news(TOPIC, DOMAINS)