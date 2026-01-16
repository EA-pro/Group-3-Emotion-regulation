#!/bin/bash

# Function to stop background processes on exit
cleanup() {
    echo "Stopping servers..."
    kill $FLASK_PID $RASA_PID $ACTION_PID 2>/dev/null
    exit
}

# Set up trap to catch Ctrl+C
trap cleanup INT

# Check if Python is installed
if ! command -v python &> /dev/null; then
    echo "Python is not installed. Please install Python 3.8 or higher."
    exit 1
fi

# Activate virtual environment if it exists
if [ -d "venv" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
fi

# Check if Rasa is installed
if ! command -v rasa &> /dev/null; then
    echo "Rasa is not installed. Please install Rasa."
    exit 1
fi

# Start Rasa server in background with explicit CORS settings
echo "Starting Rasa server with CORS enabled..."
rasa run --enable-api --cors "*" &
RASA_PID=$!

# Start Rasa Action server in background
echo "Starting Rasa Action server..."
rasa run actions &
ACTION_PID=$!

# Wait a moment to ensure servers have started
sleep 10

# Start Flask server in background
echo "Starting Flask server..."
flask run &
FLASK_PID=$!

echo "Chatbot servers are running!"
echo "- Rasa Server: http://localhost:5005"
echo "- Action Server: http://localhost:5055"
echo "- Flask Server: http://localhost:5000"
echo "Press Ctrl+C to stop all servers."

# Wait for user to press Ctrl+C
wait
