import json
import sys
import os

def clean_news(filename):
    try:
        with open(filename, 'r') as f:
            data = json.load(f)
        
        if data.get("status") != "ok":
            print(f"Error from API: {data.get('message', 'Unknown error')}")
            return

        articles = data.get("articles", [])
        
        # Create a new filename by swapping .json for .txt
        output_filename = os.path.splitext(filename)[0] + ".txt"
        
        #######
        with open(output_filename, 'w') as out_file:
            out_file.write(f"--- Top {len(articles)} Headlines ---\n\n")
            
            for i, article in enumerate(articles, 1):
                source = article['source']['name']
                title = article['title']
                url = article['url']
                
                entry = f"{i}. [{source}] {title}\n   Link: {url}\n\n"
                out_file.write(entry)
                print(entry.strip()) # Also print to terminal so you see it

        print(f"--- Success! Cleaned news saved to {output_filename} ---")
        #######

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        clean_news(sys.argv[1])