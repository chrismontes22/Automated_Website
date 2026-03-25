python news_pipeline.py
python build_site_data.py
python -m http.server 8000


workflow: get article, summarize, save to processed_articles.json, append to articles.json, build site