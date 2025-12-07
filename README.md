# Ripple Effect - Shape the City

An AI-driven civic negotiation simulator exploring how urban decisions are made. This project combines a **Flask backend** with a **Three.js 3D visualization** and **OpenAI-powered NPC agents** to simulate the complex stakeholder dynamics in urban planning.

## 🏗 Project Architecture

The system is built on a 4-level architecture:

1.  **Context**: Users (Players) interact with the System, which leverages OpenAI for intelligence.
2.  **Containers**:
    *   **Frontend**: HTML5/Tailwind/Three.js for UI and 3D rendering.
    *   **Backend**: Python Flask server handling game logic and WebSocket events.
    *   **Data**: JSON-based scenario and asset storage.
3.  **Components**:
    *   **Negotiation Engine**: Manages influence tokens, trust scores, and turn-based logic.
    *   **Persona Factory**: Generates and role-plays diverse stakeholders (Councilor, Developer, Activist).
    *   **3D Scene Manager**: Updates the urban fabric dynamically based on negotiation outcomes.

## 🚀 Key Features

*   **"Citizens as Urban Practitioners"**: Playable roles with unique objectives and resources.
*   **Influence Economy**: Strategic use of "Influence Tokens" for persuasion, pressure, or alliance building.
*   **Real-time Ripple Effect**: Negotiation outcomes directly reshape the 3D city model (e.g., building heights, green spaces).
*   **LLM-Driven NPCs**: Characters with distinct personalities, memories, and evolving stances.

## 🛠 Technology Stack

*   **Backend**: Python 3, Flask, Flask-SocketIO, OpenAI API
*   **Frontend**: Three.js, TailwindCSS, Jinja2 Templates
*   **Data**: GeoJSON, JSON

## 📦 Installation & Setup

1.  **Clone the repository** (if not already local):
    \`\`\`bash
    git clone https://github.com/YUxiaoLLL/Negotiation-Game.git
    cd Negotiation-Game
    \`\`\`

2.  **Install Dependencies**:
    \`\`\`bash
    pip install -r requirements.txt
    \`\`\`

3.  **Configure Environment**:
    Create a \`.env\` file in the root directory:
    \`\`\`
    OPENAI_API_KEY=your_api_key_here
    \`\`\`

4.  **Run the Server**:
    \`\`\`bash
    python app.py
    \`\`\`
    The application will start at \`http://localhost:5006\`.

## 📂 Project Structure

*   \`app.py\`: Main application entry point and game logic.
*   \`templates/\`: HTML templates for all game stages (Landing, Role Selection, Gaming).
*   \`new_3d_pipeline/\`: Three.js frontend and 3D data processing logic.
*   \`persona_engine.py\`: AI character generation and interaction logic.
*   \`legacy_method/\`: Archived early prototypes (React demo, old visualization scripts).

## 📄 License
[Your License Here]
