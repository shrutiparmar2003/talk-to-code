import requests
from github import Github
import os
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from flask_cors import CORS  # Install with `pip install flask-cors`

load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

app = Flask(__name__)
CORS(app)  # Enable CORS for cross-origin requests (e.g., frontend on different port)

@app.route('/')
def home():
    return jsonify({"message": "TalkToCode Backend API is running. Use /ingest, /analyze_codebase, /analyze_structure, /search, or /get_repo_data with POST requests."}), 200

@app.route('/ingest', methods=['POST'])
def ingest_repo():
    data = request.json
    repo_url = data.get('repo_url')
    exclude_patterns = data.get('exclude', [])  # List of patterns to exclude
    max_size_kb = data.get('max_size_kb', 50)  # Default 50kb
    
    if not repo_url:
        return jsonify({"error": "Missing repo_url"}), 400
    
    repo_data = fetch_repo_data(repo_url, max_files=50, exclude_patterns=exclude_patterns)
    if repo_data is None:
        return jsonify({"error": "Failed to fetch repo data"}), 500
    
    formatted_data = format_for_gemini(repo_data)
    summary = get_code_summary(formatted_data)
    suggestions = get_code_suggestions(formatted_data)
    
    return jsonify({
        "status": "success",
        "files_analyzed": len(repo_data["files"]),
        "estimated_tokens": len(formatted_data.split()),  # Rough token estimate
        "summary": summary,
        "suggestions": suggestions,
        "repo_data": repo_data  # Send raw data structure for frontend
    })

@app.route('/analyze_codebase', methods=['POST'])
def analyze_codebase():
    data = request.json
    repo_data = data.get('repo_data')
    if not repo_data:
        return jsonify({"error": "Missing repo_data"}), 400
    
    formatted_data = format_for_gemini(repo_data)
    summary = get_code_summary(formatted_data)
    return jsonify({"analysis": summary})

@app.route('/analyze_structure', methods=['POST'])
def analyze_structure():
    data = request.json
    repo_data = data.get('repo_data')
    if not repo_data:
        return jsonify({"error": "Missing repo_data"}), 400
    
    structure = "\n".join(repo_data["structure"])
    return jsonify({"structure": structure})

@app.route('/search', methods=['POST'])
def search_repo():
    data = request.json
    repo_data = data.get('repo_data')
    keyword = data.get('keyword')
    
    if not repo_data or not keyword:
        return jsonify({"error": "Missing repo_data or keyword"}), 400
    
    search_results = search_code(repo_data, keyword)
    return jsonify({"results": search_results})

@app.route('/get_repo_data', methods=['POST'])
def get_repo_data():
    data = request.json
    repo_url = data.get('repo_url')
    exclude_patterns = data.get('exclude', [])
    
    if not repo_url:
        return jsonify({"error": "Missing repo_url"}), 400
    
    repo_data = fetch_repo_data(repo_url, max_files=50, exclude_patterns=exclude_patterns)
    if repo_data is None:
        return jsonify({"error": "Failed to fetch repo data"}), 500
    
    return jsonify({
        "status": "success",
        "repo_data": repo_data,
        "files_analyzed": len(repo_data["files"])
    }), 200

def fetch_repo_data(repo_url, max_files=50, exclude_patterns=None):
    print(f"Processing URL: {repo_url}")
    if exclude_patterns is None:
        exclude_patterns = []
    repo_path = repo_url.replace("https://github.com/", "").strip("/")
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(repo_path)
    print(f"Fetched repo: {repo.full_name}")
    
    contents = repo.get_contents("")
    repo_data = {"structure": [], "files": {}}
    file_count = 0
    total_size = 0

    def traverse_directory(directory, path=""):
        nonlocal file_count, total_size
        if file_count >= max_files or total_size > 50 * 1024:  # 50kb limit
            print(f"Hit max file limit ({max_files}) or size limit (50kb)")
            return
        for content in directory:
            current_path = f"{path}/{content.name}" if path else content.name
            if any(pattern in current_path.lower() for pattern in exclude_patterns):
                print(f"Skipping excluded file: {current_path}")
                continue
            repo_data["structure"].append(current_path)
            if content.type == "file":
                try:
                    file_content = content.decoded_content.decode("utf-8", errors="ignore")
                    file_size = len(file_content.encode("utf-8"))
                    if file_count < max_files and total_size + file_size <= 50 * 1024:
                        repo_data["files"][current_path] = file_content[:500]
                        file_count += 1
                        total_size += file_size
                        print(f"Added file {file_count}: {current_path} (Size: {file_size} bytes)")
                    else:
                        print(f"Skipping {current_path} - size or limit exceeded")
                except Exception as e:
                    repo_data["files"][current_path] = f"Error: {str(e)}"
            elif content.type == "dir":
                try:
                    traverse_directory(repo.get_contents(content.path), current_path)
                except Exception as e:
                    print(f"Error in {current_path}: {str(e)}")

    traverse_directory(contents)
    print(f"Finished fetching repo data. Files: {file_count}, Total Size: {total_size} bytes")
    return repo_data

def format_for_gemini(repo_data):
    output = "Directory Structure:\n" + "\n".join(repo_data["structure"]) + "\n\n"
    output += "File Contents:\n"
    for file_path, content in repo_data["files"].items():
        output += f"--- {file_path} ---\n{content}\n"
    return output

def search_code(repo_data, keyword):
    results = []
    for file_path, content in repo_data["files"].items():
        if keyword.lower() in content.lower():
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if keyword.lower() in line.lower():
                    results.append(f"{file_path}: Line {i}: {line.strip()}")
    return results

def send_to_gemini(formatted_data, user_query, conversation_history=""):
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
    headers = {"Content-Type": "application/json"}
    full_prompt = f"Repo Data:\n{formatted_data}\n\nConversation History:\n{conversation_history}\n\nQuery: {user_query}"
    payload = {"contents": [{"parts": [{"text": full_prompt}]}]}
    response = requests.post(f"{url}?key={GEMINI_API_KEY}", json=payload, headers=headers)
    if response.status_code == 200:
        return response.json()["candidates"][0]["content"]["parts"][0]["text"]
    else:
        return f"Error: {response.status_code} - {response.text}"

def get_code_summary(formatted_data):
    summary_query = "Provide a concise summary of this repository's purpose and key files based on the provided data."
    print("Generating code summary with Gemini...")
    return send_to_gemini(formatted_data, summary_query)

def get_code_suggestions(formatted_data):
    suggestion_query = "Analyze the code in this repo data and suggest 2-3 specific improvements, additions, or fixes (e.g., add error handling, optimize a function, add documentation). Include file names where applicable."
    print("Generating code suggestions with Gemini...")
    return send_to_gemini(formatted_data, suggestion_query)

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)