import json
import os
import glob
import trafilatura

def get_latest_news_data():
    list_of_files = glob.glob('news_*.json')
    if not list_of_files:
        return None
    latest_file = max(list_of_files, key=os.path.getctime)
    with open(latest_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def process_article():
    data = get_latest_news_data()

    if not data or not data.get("articles"):
        print("No articles found to process.")
        return

    url = data["articles"][0]["url"]
    print(f"Processing URL: {url}\n" + "-"*30)

    downloaded = trafilatura.fetch_url(url)
    article_text = trafilatura.extract(
        downloaded, 
        favor_precision=True, 
        include_tables=True,
        include_comments=False
    )

    if not article_text or len(article_text) < 1000:
        content_len = len(article_text) if article_text else 0
        print(f"Skipping: Content too short or likely paywalled ({content_len} chars).")
        return

    chunk_size = 150
    if len(article_text) > chunk_size * 2:
        first_chunk = article_text[:chunk_size]
        duplicate_index = article_text.find(first_chunk, chunk_size)
        if duplicate_index != -1:
            article_text = article_text[:duplicate_index].strip()

    with open("article.txt", "w", encoding="utf-8") as f:
        f.write(article_text)
    print("Article saved to article.txt")

if __name__ == "__main__":
    process_article()