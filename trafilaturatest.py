import trafilatura

def get_article_body(url):
    # 1. Download the HTML content
    downloaded = trafilatura.fetch_url(url)
    
    if downloaded is None:
        return "Error: Could not fetch the URL."

    # 2. Extract the main text
    # include_comments=False keeps the output clean
    # output_format='txt' gives you a clean string
    result = trafilatura.extract(downloaded, include_comments=False, include_tables=True)
    
    return result

# Example usage
target_url = "https://hbr.org/2026/03/competing-llms-were-asked-to-pick-stocks-their-choices-revealed-ais-limitations"
body_text = get_article_body(target_url)

print(body_text)