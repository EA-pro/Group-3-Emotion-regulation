import json
import os
import requests
import logging
from flask import Flask, send_from_directory, request, jsonify
from flask_cors import CORS
from requests.exceptions import RequestException, Timeout, ConnectionError

# Setup logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__, static_folder='frontend')

CORS(app, resources={r"/*": {"origins": "*"}})

# Default Rasa server URL
DEFAULT_RASA_URL = 'http://localhost:5005/webhooks/rest/webhook'

# Serve frontend files
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_static(path):
    if path and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/api/check_rasa', methods=['GET'])
def check_rasa():
    rasa_url = os.environ.get('RASA_URL', 'http://localhost:5005')
    try:
        # Try to connect to the server's health endpoint
        response = requests.get(f"{rasa_url}/version", timeout=3)
        if response.ok:
            return jsonify({"status": "available", "version": response.json()})
        else:
            return jsonify({"status": "unavailable", "reason": "API responded with error"}), 503
    except RequestException as e:
        logger.error(f"Failed to connect to Rasa server: {str(e)}")
        return jsonify({"status": "unavailable", "reason": str(e)}), 503


# Handle Rasa messages
@app.route('/api/send_message', methods=['POST'])
def send_message():
    data = request.json
    message = data.get('message')
    context = data.get('context', {})
    sender = data.get('sender', 'user')

    rasa_url = os.environ.get('RASA_URL', DEFAULT_RASA_URL)
    payload = {
        "sender": sender,
        "message": message,
        "metadata": context
    }

    try:
        response = requests.post(rasa_url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        return jsonify(process_rasa_response(data, context))
    except RequestException as e:
        error_message = f"Error communicating with Rasa server: {str(e)}"
        logger.error(error_message)
        return jsonify({
            "error": error_message,
            "context": context,
            "messages": [{"text": "I'm sorry, I encountered an error processing your request. Please try again later."}]
        }), 500


def process_rasa_response(response, original_context):
    result = {
        "messages": [],
        "context": {**original_context},
        "actions": []
    }

    if not response or len(response) == 0:
        result["messages"].append(
            {"text": "I didn't receive a proper response. Please try again."})
        return result

    for item in response:
        if item.get("text"):
            result["messages"].append({"text": item["text"]})
            
        # Check for json_message format which is used by newer Rasa SDK
        if item.get("json_message"):
            json_data = item["json_message"]
            
            # Extract action information
            if json_data.get("action"):
                action = json_data["action"]
                result["actions"].append(action)
            
            # Update context with any new information
            if json_data.get("context"):
                result["context"].update(json_data["context"])
                
            # Continue processing other parts of the message
            continue

        # Handle legacy custom format
        if item.get("custom"):
            custom_data = item["custom"]
            if isinstance(custom_data, str):
                try:
                    custom_data = json.loads(custom_data)
                except json.JSONDecodeError:
                    logger.warning(
                        f"Failed to decode custom JSON: {custom_data}")
                    continue

            if custom_data.get("action"):
                result["actions"].append(custom_data["action"])
                
            if custom_data.get("context"):
                result["context"].update(custom_data["context"])

        if item.get("image"):
            result["messages"].append({"type": "image", "url": item["image"]})

        if item.get("buttons"):
            last_message = result["messages"][-1] if result["messages"] else {
                "text": ""}
            last_message["buttons"] = item["buttons"]
            if not result["messages"] or result["messages"][-1] != last_message:
                result["messages"].append(last_message)

    return result

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    logger.info(f"Chatbot server running on http://localhost:{port}")
    logger.info("To use with Rasa, make sure to start the Rasa server with:")
    logger.info("  - rasa run --enable-api --cors \"*\"")
    app.run(debug=True, port=port)
