import os
import requests
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

# --- CONFIGURATION ---
BASE_URL = "https://newsapi.org/v2/everything"
# Updated to a list for iteration
TOPICS_LIST = ["stocks", "economy", "prices", "money", "finance"]
DOMAINS = ""
PAGE_SIZE = 100
SORT_BY = "popularity"

def fetch_popular_news(topics, domains):
    # Load the .env file and grab the key
    load_dotenv()
    api_key = os.getenv("NEWS_API_KEY")
    
    if not api_key:
        print("Error: NEWS_API_KEY not found in .env file")
        return

    # Calculate the window (UTC)
    now = datetime.utcnow()
    from_date = (now - timedelta(hours=37)).isoformat()
    to_date = (now - timedelta(hours=25)).isoformat()

    print(f"Time Window: {from_date} to {to_date}\n")

# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #
    for topic in topics:
        print(f"Fetching news for: {topic}...")
        
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

        try:
            response = requests.get(BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

            # Create a timestamp and a safe filename using the topic name
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            safe_topic = topic.replace(" ", "_").lower()
            filename = f"news_{safe_topic}_{timestamp}.json"

            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4)
            
            print(f"Successfully saved {topic} news to {filename}")

        except requests.exceptions.RequestException as e:
            print(f"Error fetching news for '{topic}': {e}")
# # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # # #

if __name__ == "__main__":
    fetch_popular_news(TOPICS_LIST, DOMAINS)