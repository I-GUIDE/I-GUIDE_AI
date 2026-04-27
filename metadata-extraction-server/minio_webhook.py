import os
import subprocess
from flask import Flask, request, jsonify

app = Flask(__name__)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

@app.route('/webhook', methods=['POST'])
def webhook():
    event = request.json
    print("Received event:", event)
    
    # Trigger metadata extraction script
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']
    script = os.path.join(_SCRIPT_DIR, "extract_metadata_code_notebooks.py")
    subprocess.run(["python3", script, bucket, key])
    
    return jsonify({"message": "Event received"}), 200

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)