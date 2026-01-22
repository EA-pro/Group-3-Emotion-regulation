#!/usr/bin/env bash
set -euo pipefail

# Stop background processes on exit
cleanup() {
  echo "Stopping servers..."
  kill ${FLASK_PID:-0} ${RASA_PID:-0} ${ACTION_PID:-0} 2>/dev/null || true
}
trap cleanup INT TERM

# Activate virtual environment (.venv preferred)
if [ -d ".venv" ]; then
  echo "Activating .venv..."
  source .venv/bin/activate
elif [ -d "venv" ]; then
  echo "Activating venv..."
  source venv/bin/activate
fi

# Load environment variables
if [ -f ".env" ]; then
  echo "Loading environment from .env..."
  set -a
  source .env
  set +a
fi

# Ensure deps exist
command -v python >/dev/null || { echo "Python is not installed."; exit 1; }
command -v rasa >/dev/null || { echo "Rasa is not installed."; exit 1; }
command -v flask >/dev/null || { echo "Flask is not installed."; exit 1; }

export FLASK_APP=app

echo "Starting Rasa server with CORS enabled..."
rasa run --enable-api --cors "*" &
RASA_PID=$!

echo "Starting Rasa Action server..."
rasa run actions &
ACTION_PID=$!

sleep 5

echo "Starting Flask server..."
flask run &
FLASK_PID=$!

echo "Chatbot servers are running!"
echo "- Rasa Server: http://localhost:5005"
echo "- Action Server: http://localhost:5055"
echo "- Flask Server: http://localhost:5000"
echo "Press Ctrl+C to stop all servers."

wait
