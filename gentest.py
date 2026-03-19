import os
from dotenv import load_dotenv
from google import genai

###
load_dotenv()
api_key = os.getenv("GEMINI_KEY")

client = genai.Client(api_key=api_key)

# Directly paste your text between the triple quotes below
article_text = """
            "source": {
                "id": null,
                "name": "CoinDesk"
            },
            "author": "Omkar Godbole",
            "title": "Bitcoin hits a wall at $75,000 while onchain energy markets run hot",
            "description": "The day ahead in crypto: March 17, 2026",
            "url": "https://www.coindesk.com/daybook-us/2026/03/17/bitcoin-hits-a-wall-at-usd75-000-while-onchain-energy-markets-run-hot",
            "urlToImage": "https://cdn.sanity.io/images/s3y3vcno/production/adcbf9436197816692e7e617ee5d6bd1e8fd1255-1920x1080.jpg?auto=format",
            "publishedAt": "2026-03-17T11:23:03Z",
            "content": "By Omkar Godbole (All times ET unless indicated otherwise)\r\nWhile bitcoins BTC\r\n$73,830.48 price rise since the Iran conflict began more than two weeks ago is impressive, the performance of Hyperliqu\u2026 [+7681 chars]"
"""

response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=f"You are an ai agent, so your output will be put into code. This means you must answer with only one of the following categories to describe the tect, and it must be exactly as written: ai economics gadgets. Text: {article_text}"
)

print(response.text)
###

# To use this in a single flow, you would call: