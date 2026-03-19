import json
import os
import glob
from urllib.parse import urlparse

def get_source_name(article):
    """Extracts the source name (e.g., 'The Verge') from an article."""
    try:
        return article.get("source", {}).get("name", "Unknown")
    except:
        return "Unknown"

def create_master_json():
    # 1. Find all JSON files from the latest fetch session
    # We grab 'news_*.json' files
    all_json_files = glob.glob('news_*.json')
    
    if not all_json_files:
        print("No news files found to merge.")
        return

    # 2. Load all articles into a list of lists (one list per topic)
    topic_queues = []
    for file in all_json_files:
        with open(file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            articles = data.get("articles", [])
            if articles:
                topic_queues.append(articles)

    master_list = []
    source_counts = {}
    seen_urls = set()
    
    # CONFIGURATION: How many articles per source and total articles?
    SOURCE_LIMIT = 1
    TOTAL_GOAL = 50 

    # 3. Round Robin Interleaving Logic
    # We keep looping as long as we have articles to pick and haven't hit our goal
    max_iterations = max(len(q) for q in topic_queues) if topic_queues else 0
    
    for i in range(max_iterations):
        for queue in topic_queues:
            if i < len(queue):
                article = queue[i]
                url = article.get("url")
                source_name = get_source_name(article)

                # --- THE BOUNCER CHECKS ---
                # Check 1: Already in the list? (Deduplication by URL)
                if url in seen_urls:
                    continue
                
                # Check 2: Too many from this source? (Diversity by source.name)
                if source_counts.get(source_name, 0) >= SOURCE_LIMIT:
                    continue
                
                # --- ADD TO MASTER ---
                master_list.append(article)
                seen_urls.add(url)
                source_counts[source_name] = source_counts.get(source_name, 0) + 1

                # Stop if we hit a massive number
                if len(master_list) >= TOTAL_GOAL:
                    break
        if len(master_list) >= TOTAL_GOAL:
            break

    # 4. Save the high-quality, interleaved list to a single file
    # This master file becomes the single source of truth for your next script
    output_data = {
        "status": "ok",
        "totalResults": len(master_list),
        "articles": master_list
    }

    with open("master_news.json", 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=4)

    print(f"Master JSON created with {len(master_list)} unique, diverse articles.")
    
    # Optional: Show source distribution
    print("\nSource distribution:")
    for source, count in sorted(source_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {source}: {count} article(s)")

if __name__ == "__main__":
    create_master_json()