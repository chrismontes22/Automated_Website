To test locally on Powershell

python news_pipeline.py
python build_site_data.py
python -m http.server 8000


Workflow:
get article, summarize, save to processed_articles.json, append to articles.json, build site from articles.json/index.html