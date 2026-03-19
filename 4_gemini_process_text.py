import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

###
def summarize_with_gemini(article_text):
    if not article_text:
        print("No article text provided to summarize.")
        return

    api_key = os.getenv("GEMINI_KEY")
###
    if not api_key:
        raise ValueError("GEMINI_KEY not found in .env file.")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=f"Please summarize and SIMPLIFY the following article in a clear and easy to read manner:\n\n{article_text}",
        config={'max_output_tokens': 2000, 'temperature': 0.7}
    )

    summary = response.text
    print(summary)

    with open("summary.txt", "w", encoding="utf-8") as f:
        f.write(summary)
    print("\nSummary saved to summary.txt")

# To use this in a single flow, you would call:
# text = process_article() <-- Make sure process_article returns article_text
# summarize_with_gemini(text)