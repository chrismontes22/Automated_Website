import json
import os
import glob

def get_latest_url():
    # Find all json files starting with 'news_'
    list_of_files = glob.glob('news_*.json')
    
    if not list_of_files:
        print("No news files found.")
        return

    # Get the most recent file based on creation time
    latest_file = max(list_of_files, key=os.path.getctime)

    try:
        with open(latest_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        ###
        # Access the first article and its URL
        if data.get("articles") and len(data["articles"]) > 0:
            first_url = data["articles"][0]["url"]
            print(first_url)
        else:
            print("No articles found in the JSON.")
        ###
            
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"Error processing JSON: {e}")

if __name__ == "__main__":
    get_latest_url()