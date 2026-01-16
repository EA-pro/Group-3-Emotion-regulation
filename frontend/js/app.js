// Global voice-related variables and functions
let recognition = null;
let isListening = false;
let synth = window.speechSynthesis;
let isSpeaking = false;

/**
 * Toggle speech recognition
 */
function toggleSpeechRecognition() {
    if (isListening) {
        recognition.stop();
    } else {
        // Clear any existing text
        document.querySelector('.chat-input textarea').value = '';
        recognition.start();
    }
}

speakTextQueue = [];

/**
 * Speak text using text-to-speech
 * @param {string} text - Text to be spoken
 */
function speakText(text) {
    if (isSpeaking) {
        speakTextQueue.push(text);
        return;
    }

    // Cancel any ongoing speech
    synth.cancel();
    
    // Get checkbox state directly from DOM
    const autoReadCheckbox = document.getElementById('autoReadResponses');
    
    // Only continue if auto-read is enabled
    if (!autoReadCheckbox || !autoReadCheckbox.checked) return;

    isSpeaking = true;
    
    // Clean text of HTML tags
    const cleanText = text.replace(/<[^>]*>?/gm, '');
    
    // Create a new utterance
    const utterance = new SpeechSynthesisUtterance(cleanText);
    
    // Try to get a more natural voice
    let voices = synth.getVoices();
    let preferredVoice = voices.find(voice => 
        voice.name.includes('Samantha') || 
        voice.name.includes('Google') || 
        voice.name.includes('Natural') ||
        (voice.name.includes('US') && voice.name.includes('Female'))
    );
    
    if (preferredVoice) {
        utterance.voice = preferredVoice;
    }
    
    utterance.lang = 'en-US';
    utterance.rate = 0.9; // Slightly slower rate for more natural sound
    utterance.pitch = 1.0;
    utterance.volume = 1.0;
    
    utterance.onstart = () => {
        console.log('Speaking with voice:', utterance.voice ? utterance.voice.name : 'Default voice');
    };
    
    utterance.onend = () => {
        isSpeaking = false;

        if (speakTextQueue.length > 0) {
            const nextText = speakTextQueue.shift();
            speakText(nextText);
        }
    };
    
    utterance.onerror = (event) => {
        console.error('Speech synthesis error', event);
        isSpeaking = false;
    };
    
    // Speak the text
    synth.speak(utterance);
}

// Make the functions available globally
window.toggleSpeechRecognition = toggleSpeechRecognition;
window.speakText = speakText;

document.addEventListener('DOMContentLoaded', () => {
    // Check if required dependencies are loaded
    if (!window.DB) {
        console.error('DB not initialized. Please check db.js is loaded correctly.');
        alert('Application failed to initialize database. Please refresh the page or contact support.');
        return;
    }

    // DOM Elements
    const chatMessages = document.querySelector('.chat-messages');
    const chatInput = document.querySelector('.chat-input textarea');
    const sendButton = document.querySelector('.send-button');
    const micButton = document.querySelector('.mic-button');
    const voiceIndicator = document.querySelector('.voice-indicator');
    const voiceControls = document.querySelector('.voice-controls');
    const clearButton = document.querySelector('.btn-trash');

    // State
    let isTyping = false;
    let conversationContext = {};
    let senderId = 'user_' + Math.random().toString(36).substring(2);

    // Initialize
    chatInput.focus();
    scrollToBottom();
    initChatbot();

    // Event Listeners
    chatInput.addEventListener('input', autoResizeTextarea);
    chatInput.addEventListener('keydown', handleInputKeydown);
    sendButton.addEventListener('click', sendMessage);
    clearButton.addEventListener('click', clearChat);

    // Initialize the voices as soon as possible
    function initVoices() {
        return new Promise((resolve) => {
            if (synth.getVoices().length > 0) {
                resolve(synth.getVoices());
                return;
            }
            
            synth.onvoiceschanged = () => {
                resolve(synth.getVoices());
            };
        });
    }
    
    // Initialize voices
    initVoices().then(voices => {
        console.log('Available voices:', voices.map(v => v.name).join(', '));
    });
    
    // Initialize speech recognition if supported
    if ('SpeechRecognition' in window || 'webkitSpeechRecognition' in window) {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        recognition = new SpeechRecognition();
        recognition.continuous = false;
        recognition.interimResults = true;
        recognition.lang = 'en-US';

        recognition.onstart = () => {
            isListening = true;
            micButton.classList.add('listening');
            voiceIndicator.style.display = 'block';
        };

        recognition.onresult = (event) => {
            const transcript = Array.from(event.results)
                .map(result => result[0])
                .map(result => result.transcript)
                .join('');
            
            chatInput.value = transcript;
            autoResizeTextarea();
        };

        recognition.onend = () => {
            isListening = false;
            micButton.classList.remove('listening');
            voiceIndicator.style.display = 'none';
            
            // If we have content, send after a short delay to allow user to see what was recognized
            if (chatInput.value.trim()) {
                setTimeout(() => {
                    sendMessage();
                }, 1000);
            }
        };

        recognition.onerror = (event) => {
            console.error('Speech recognition error', event.error);
            isListening = false;
            micButton.classList.remove('listening');
            voiceIndicator.style.display = 'none';
        };

        // Add event listener for the mic button
        micButton.addEventListener('click', toggleSpeechRecognition);
        
        // Show voice controls since speech is supported
        if (voiceIndicator) {
            voiceIndicator.style.display = 'flex';
            console.log('Voice controls should be visible');
        }
    } else {
        // Hide mic button if speech recognition is not supported
        if (micButton) micButton.style.display = 'none';
        if (voiceIndicator) voiceIndicator.style.display = 'none';
        console.warn('Speech recognition not supported in this browser');
    }

    /**
     * Initialize Chatbot and its services
     */
    async function initChatbot() {
        try {
            console.log('Initializing Chatbot...');
            // Load previous conversations for context
            const conversations = await loadRecentConversations();
            conversations.forEach((conversation) => {
                addMessageToChat(conversation.sender, conversation.message, Date.parse(conversation.timestamp), false);
            })

            console.log('Chatbot initialization complete');
        } catch (error) {
            console.error('Error initializing Chatbot:', error);
        }
    }

    /**
     * Load recent conversations from the database
     */
    async function loadRecentConversations() {
        try {
            const conversations = (await window.DB.getConversations())
                .sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

            // Only load the last 10 conversations if there are any
            if (conversations && conversations.length > 0) {
                const recentConversations = conversations.slice(-10);

                // Update context with historical data
                conversationContext.history = recentConversations;
                console.log('Loaded conversation history for context:', conversationContext);
            }

            return conversations;
        } catch (error) {
            console.error('Error loading conversations:', error);
        }
    }

    /**
     * Auto-resize textarea as user types
     */
    function autoResizeTextarea() {
        chatInput.style.height = 'auto';
        chatInput.style.height = chatInput.scrollHeight + 'px';
    }

    /**
     * Handle keydown in the textarea (e.g., Enter to send)
     */
    function handleInputKeydown(e) {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    }

    /**
     * Send user message and get bot response
     */
    function sendMessage() {
        const message = chatInput.value.trim();
        if (!message) return;

        // Current timestamp
        const timestamp = new Date();

        // Add user message to chat
        addMessageToChat('user', message, timestamp);

        // Save user message to database
        window.DB.saveConversation({
            sender: 'user',
            message: message,
            context: conversationContext,
            timestamp: timestamp.toISOString() // Explicit timestamp
        });

        // Clear input
        chatInput.value = '';
        chatInput.style.height = 'auto';

        // Show typing indicator
        showTypingIndicator();
        const typingStartTime = Date.now();

        // Send message to Python backend and get response
        const fetchPromise = fetch('/api/send_message', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ message: message, context: conversationContext, sender: senderId })
        })
            .then(response => {
                if (!response.ok) {
                    throw new Error(`Server responded with ${response.status}: ${response.statusText}`);
                }
                return response.json();
            });

        // Wait for both the response AND minimum 1 second typing delay
        const minDelayPromise = new Promise(resolve => {
            const elapsed = Date.now() - typingStartTime;
            const remaining = Math.max(0, 1000 - elapsed);
            setTimeout(resolve, remaining);
        });

        Promise.all([fetchPromise, minDelayPromise])
            .then(([data]) => {
                console.log("Response from Rasa:", data); // Debug: log the response
                const responseTimestamp = new Date();
                hideTypingIndicator();

                // Check if data is in the expected format
                if (data.fallback_response) {
                    // Handle fallback response (error case)
                    addMessageToChat('bot', data.fallback_response[0].text, responseTimestamp);
                    return;
                }

                // Process Rasa response
                handleRasaResponse(data, responseTimestamp);
            })
            .catch(error => {
                const errorTimestamp = new Date();
                console.error('Error getting response from Rasa:', error);
                hideTypingIndicator();
                addMessageToChat('bot', 'Sorry, I encountered a network error. Please try again later.', errorTimestamp);
            });
    }

    /**
     * Send a button payload to the backend
     * @param {string} payload - The payload string (e.g.,
     "/mood_angry{\"mood\":\"angry\"}")
     * @param {string} buttonTitle - The button title to display (e.g., "😡 Angry" or "I feel frustrated")
    */
    function sendPayloadMessage(payload, buttonTitle = null) {
        const timestamp = new Date();

        // Use button title if provided, otherwise parse payload
        const displayText = buttonTitle || (payload.match(/^\/([a-z_]+)/) ? payload.match(/^\/([a-z_]+)/)[1].replace(/_/g, ' ') : payload);

        // Show user's choice in chat (shows what button they clicked)
        addMessageToChat('user', displayText, timestamp);

        // Save to database
        window.DB.saveConversation({
            sender: 'user',
            message: payload,
            context: conversationContext,
            timestamp: timestamp.toISOString()
        });

        showTypingIndicator();
        const typingStartTime = Date.now();

        const fetchPromise = fetch('/api/send_message', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                message: payload,
                context: conversationContext,
                sender: senderId
            })
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`Server responded with ${response.status}:
    ${response.statusText}`);
            }
            return response.json();
        });

        // Wait for both the response AND minimum 1 second typing delay
        const minDelayPromise = new Promise(resolve => {
            const elapsed = Date.now() - typingStartTime;
            const remaining = Math.max(0, 1000 - elapsed);
            setTimeout(resolve, remaining);
        });

        Promise.all([fetchPromise, minDelayPromise])
        .then(([data]) => {
            const responseTimestamp = new Date();
            hideTypingIndicator();
            handleRasaResponse(data, responseTimestamp);
        })
        .catch(error => {
            console.error('Error sending payload:', error);
            hideTypingIndicator();
            addMessageToChat('bot', 'Sorry, I encountered an error.', new Date());
        });
    }

    /**
     * Handle Rasa response and perform actions based on it
     * @param {Object} response - Response from Rasa backend
     * @param {Date} timestamp - Timestamp when response was received
     */
    function handleRasaResponse(response, timestamp) {
        console.log("Processing response:", response);
        
        // Check if response is an array (direct messages)
        if (Array.isArray(response)) {
            response.forEach(message => {
                if (message.text) {
                    const buttons = message.buttons || null;
                    addMessageToChat('bot', message.text, timestamp, true, buttons);

                    // Save bot response to database
                    window.DB.saveConversation({
                        sender: 'bot',
                        message: message.text,
                        context: conversationContext,
                        timestamp: timestamp.toISOString()
                    });
                }
            });
            return;
        }
        
        // Handle messages
        if (response.messages && response.messages.length > 0) {
            response.messages.forEach(message => {
                if (message.text) {
                    const buttons = message.buttons || null;
                    addMessageToChat('bot', message.text, timestamp, true, buttons);

                    // Save bot response to database
                    window.DB.saveConversation({
                        sender: 'bot',
                        message: message.text,
                        context: response.context || conversationContext,
                        timestamp: timestamp.toISOString()
                    });
                }
            });
        }

        // Process actions from Rasa
        if (response.actions && response.actions.length > 0) {
            response.actions.forEach(action => {
                executeAction(action, response.context);
            });
        }

        // Update conversation context
        if (response.context) {
            conversationContext = {
                ...conversationContext,
                ...response.context
            };
        }
        
        // If no response was processed, show a fallback message
        if ((!response.messages || response.messages.length === 0) && 
            (!response.actions || response.actions.length === 0)) {
            addMessageToChat('bot', "I'm processing your request. Please give me a moment.", timestamp);
        }
    }

    /**
     * Execute actions received from Rasa
     * @param {Object} action - Action to execute
     * @param {Object} context - Context for the action
     */
    function executeAction(action, context) {
        switch (action.name) {
            default:
                console.log('Unknown action:', action.name);
        }
    }

    /**
     * Format timestamp to human-readable string
     * @param {Date} timestamp - The timestamp to format
     * @returns {string} - Formatted timestamp string
     */
    function formatTimestamp(timestamp) {
        // If it's not a Date object, try to convert it
        if (!(timestamp instanceof Date)) {
            timestamp = new Date(timestamp);
        }

        // Check if timestamp is today
        const now = new Date();
        const isToday = timestamp.toDateString() === now.toDateString();

        // Format options
        const timeOptions = { hour: '2-digit', minute: '2-digit' };
        const dateTimeOptions = {
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        };

        // If it's today, just show the time, otherwise show date and time
        return timestamp.toLocaleString(undefined, isToday ? timeOptions : dateTimeOptions);
    }

    /**
     * Add a message to the chat
     */
    function addMessageToChat(sender, text, timestamp = new Date(), newMessage = true, buttons = null) {
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message');
        messageDiv.classList.add(sender === 'user' ? 'user-message' : 'bot-message');

        const avatarDiv = document.createElement('div');
        avatarDiv.classList.add('message-avatar');

        const avatarIcon = sender === 'user'
            ? document.createElement('i')
            : document.createElement('img');

        if (sender === 'user') {
            avatarIcon.classList.add('fa-solid', 'fa-user');
        } else {
            avatarIcon.src = 'images/happy-robot.svg';
            avatarIcon.alt = 'Bot';
            avatarIcon.classList.add('bot-avatar-icon');
        }

        avatarDiv.appendChild(avatarIcon);
        messageDiv.appendChild(avatarDiv);

        const contentDiv = document.createElement('div');
        contentDiv.classList.add('message-content');

        const textDiv = document.createElement('div');
        textDiv.classList.add('message-text');

        // Format the text content properly
        // Check if the text contains HTML and handle accordingly
        if (/<[a-z][\s\S]*>/i.test(text)) {
            // If the text contains HTML, set it as innerHTML
            textDiv.innerHTML = text;
        } else {
            // Convert plain text to HTML with line breaks and formatting
            const formattedText = text
                .replace(/\n/g, '<br>')
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                .replace(/\*(.*?)\*/g, '<em>$1</em>')
                .replace(/```(.*?)/gs, '<pre><code>$1</code></pre>')
                .replace(/`(.*?)`/g, '<code>$1</code>');
            textDiv.innerHTML = formattedText;
        }

        const timeDiv = document.createElement('div');
        timeDiv.classList.add('message-time');
        timeDiv.textContent = formatTimestamp(timestamp);

        contentDiv.appendChild(textDiv);
        contentDiv.appendChild(timeDiv);
        messageDiv.appendChild(contentDiv);

        if (buttons && buttons.length > 0) {
            const buttonsContainer = document.createElement('div');
            buttonsContainer.classList.add('chat-suggestions');

            buttons.forEach(button => {
                const buttonElement = document.createElement('button');
                buttonElement.classList.add('suggestion-chip');
                buttonElement.textContent = button.title;
                buttonElement.addEventListener('click', () => {
                    const payload = button.payload;
                    sendPayloadMessage(payload, button.title);

                    buttonsContainer.querySelectorAll('.suggestion-chip').forEach(btn => {
                        btn.disabled = true;
                        btn.style.opacity = '0.5';
                        btn.style.cursor = 'not-allowed';
                    });
                });

                buttonsContainer.appendChild(buttonElement);
            });

            contentDiv.appendChild(buttonsContainer);
        }

        chatMessages.appendChild(messageDiv);
        scrollToBottom();

        // If it's a bot message, read it out loud
        if (sender === 'bot' && newMessage) {
            window.speakText(text);
        }
    }

    /**
     * Show typing indicator
     */
    function showTypingIndicator() {
        if (isTyping) return;

        isTyping = true;
        const typingDiv = document.createElement('div');
        typingDiv.classList.add('message', 'bot-message', 'typing-message');

        const avatarDiv = document.createElement('div');
        avatarDiv.classList.add('message-avatar');

        const avatarIcon = document.createElement('img');
        avatarIcon.src = 'images/happy-robot.svg';
        avatarIcon.alt = 'Bot';
        avatarIcon.classList.add('bot-avatar-icon');

        avatarDiv.appendChild(avatarIcon);
        typingDiv.appendChild(avatarDiv);

        const contentDiv = document.createElement('div');
        contentDiv.classList.add('message-content');

        const textDiv = document.createElement('div');
        textDiv.classList.add('message-text');

        const typingIndicator = document.createElement('div');
        typingIndicator.classList.add('typing-indicator');

        for (let i = 0; i < 3; i++) {
            const dot = document.createElement('div');
            dot.classList.add('typing-dot');
            typingIndicator.appendChild(dot);
        }

        textDiv.appendChild(typingIndicator);
        contentDiv.appendChild(textDiv);
        typingDiv.appendChild(contentDiv);

        chatMessages.appendChild(typingDiv);
        scrollToBottom();
    }

    /**
     * Hide typing indicator
     */
    function hideTypingIndicator() {
        const typingMessage = document.querySelector('.typing-message');
        if (typingMessage) {
            typingMessage.remove();
        }
        isTyping = false;
    }

    /**
     * Clear chat messages with confirmation
     */
    async function clearChat() {
        // Show a confirmation dialog
        const confirmClear = confirm("Are you sure you want to clear the chat history? This action cannot be undone.");

        if (confirmClear) {
            const messages = Array.from(document.querySelectorAll('.message'));
            messages.forEach(msg => msg.remove());

            // Clear conversations from database and wait for it to complete
            await clearConversationsFromDB();

            // Reset conversation context
            conversationContext = {};

            // Generate new sender ID for fresh Rasa conversation
            senderId = 'user_' + Math.random().toString(36).substring(2);

            console.log('Chat cleared successfully');
        }
    }

    /**
     * Clear conversations from the database
     */
    async function clearConversationsFromDB() {
        try {
            await window.DB.db.conversations.clear();
            console.log('Deleted all conversations from database');

            // Verify the database is actually empty
            const count = await window.DB.db.conversations.count();
            console.log('Conversations remaining in DB:', count);

            if (count === 0) {
                console.log('Database successfully cleared!');
            } else {
                console.error('Database still has conversations after clearing!');
            }
        } catch (error) {
            console.error('Error clearing conversations from database:', error);
        }
    }

    /**
     * Scroll to bottom of chat messages
     */
    function scrollToBottom() {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
});


// Make the function available globally
window.speakText = speakText;

// Helper function for LLM action buttons to send messages to Rasa
function sendToRasa(message) {
    // Create a timestamp
    const timestamp = new Date();

    // Show message in chat
    addMessageToChat('user', message, timestamp);

    // Show typing indicator
    showTypingIndicator();

    // Send to Rasa
    fetch('/api/send_message', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ message: message, context: conversationContext })
    })
    .then(response => {
        if (!response.ok) {
            throw new Error(`Server responded with ${response.status}: ${response.statusText}`);
        }
        return response.json();
    })
    .then(data => {
        const responseTimestamp = new Date();
        hideTypingIndicator();
        
        // Process response
        handleRasaResponse(data, responseTimestamp);
    })
    .catch(error => {
        console.error('Error communicating with Rasa:', error);
        hideTypingIndicator();
        addMessageToChat('bot', 'Sorry, I encountered an error processing that request.', new Date());
    });
}

// Make sendToRasa available globally
window.sendToRasa = sendToRasa;