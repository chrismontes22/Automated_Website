import subprocess
import sys

def execute_task(script_path):
    print(f"Executing: {script_path}")
    try:
        # Run the script using the current Python interpreter
        subprocess.run([sys.executable, script_path], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error: {script_path} failed with exit code {e.returncode}")
        sys.exit(1)

if __name__ == "__main__":
    # Define the sequence of scripts to run
    ###
    execute_task("1_get_articles_test.py")
    execute_task("2_extract_url.py")
    execute_task("3_extract_full_article.py")
    execute_task("4_gemini_process_text.py")
    ###
    
    print("Success: Both scripts ran in order.")