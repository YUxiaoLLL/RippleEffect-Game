from flask import Flask, render_template, Response, request, jsonify, session, send_from_directory, redirect, url_for, flash
from flask_socketio import SocketIO
from typing import List
import random
import os
import json
import requests
import math
from collections import Counter
from flask_session import Session  # Import Flask-Session
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path
from models import SceneState, Block, Action, SceneUpdate
from agents.persona_engine import generate_dna_persona
from agents.persona_data import STYLES # Import STYLES dictionary
# import ezdxf
from werkzeug.utils import secure_filename

# Load environment variables from .env file
dotenv_path = Path('.') / '.env'  # Explicitly point to .env in current directory
load_dotenv(dotenv_path=dotenv_path)

# --- Debug: Check if API key is loaded --- #
print(f"DEBUG: OPENAI_API_KEY loaded from environ: {os.environ.get('OPENAI_API_KEY')}")
# --- End Debug --- #

# --- Setup ---
# Calculate absolute paths to ensure Flask finds templates/static regardless of where the script is run from
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # Directory of server.py (backend/)
PROJECT_ROOT = os.path.dirname(BASE_DIR) # Root directory (RippleEffect/)
TEMPLATE_DIR = os.path.join(PROJECT_ROOT, 'frontend', 'templates')
STATIC_DIR = os.path.join(PROJECT_ROOT, 'frontend', 'static')
THREE_JS_DIR = os.path.join(PROJECT_ROOT, 'frontend', 'static', '3d_client')
THREE_DATA_DIR = os.path.join(PROJECT_ROOT, 'frontend', 'static', '3d_data')

print(f"DEBUG: BASE_DIR: {BASE_DIR}")
print(f"DEBUG: PROJECT_ROOT: {PROJECT_ROOT}")
print(f"DEBUG: TEMPLATE_DIR: {TEMPLATE_DIR}")
print(f"DEBUG: STATIC_DIR: {STATIC_DIR}")
print(f"DEBUG: THREE_JS_DIR: {THREE_JS_DIR}")
print(f"DEBUG: Does THREE_JS_DIR exist? {os.path.isdir(THREE_JS_DIR)}")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
socketio = SocketIO(app, cors_allowed_origins="*")
app.secret_key = os.urandom(24)  # More secure secret key

# --- Server-Side Session Configuration ---
app.config['SESSION_TYPE'] = 'filesystem'  # Store session data in files
app.config['SESSION_PERMANENT'] = False  # Session expires when browser closes
app.config['SESSION_USE_SIGNER'] = True  # Encrypt session cookie identifier
app.config['SESSION_FILE_DIR'] = './.flask_session'  # Optional: Specify directory
Session(app)  # Initialize the session extension


# --- Game Constants ---
STANCES = {
    "support": "Support",
    "oppose": "Oppose",
    "neutral": "Neutral",
    "compromise": "Compromise"
}
NEUTRAL_SCORE = 50 # Default neutral score
MAX_ROUNDS = 8
MIN_STATEMENT_WORDS = 15  # New constant
EVENT_PROBABILITY = 0.25  # 25% chance of an event each round
TOKEN_REGEN_RATE = 2  # How many influence tokens characters regain each round
INITIAL_TRUST = 50  # Default starting trust value (0-100)
MAX_PLAYER_TOKENS = 12  # Maximum tokens the player can hold
BASE_LEAK_CHANCE = 0.4 # 40% chance for pressure to leak
POLARIZATION_SPREAD_IMPACT = 4 # Impact on others if pressure leaks

INFLUENCE_ACTION_COSTS = {
    "gentle_persuasion": 1,
    "pressure_opponent": 2,
    "strong_persuasion": 3,
    "ally_recruitment": 4
}
INFLUENCE_ACTION_EFFECTS = {
    # Stance delta is towards player's general alignment (support/oppose project)
    # Needs refinement - assumes player wants to pull target towards their stance.
    # Simple approach: Positive delta = more support, Negative delta = more opposition.
    # TODO: Make delta relative to player's stance vs target's stance.
    "gentle_persuasion": {"stance_delta": 5, "trust_delta": 2, "history_log": "gently persuaded"},
    "strong_persuasion": {"stance_delta": 15, "trust_delta": 10, "history_log": "strongly persuaded"},
    "ally_recruitment": {"stance_delta": 0, "trust_delta": 15, "history_log": "tried to recruit"},
    # Focus on trust gain for now
    "pressure_opponent": {"stance_delta": -10, "trust_delta": -15, "history_log": "pressured"},
    # Makes target more opposed/less supportive
}
INITIAL_SUPPORT_SCORE = 75
INITIAL_NEUTRAL_SCORE = 50
INITIAL_OPPOSE_SCORE = 25
INFLUENCE_SCORES = {
    "developer": 3,
    "resident_homeowner": 2,
    "resident_social": 2,
    "future_buyer": 2,
    "community_activist": 2,
    "council_planner": 3,
    "urban_designer": 2
}
VICTORY_CONSENSUS_THRESHOLD = 6  # 6 out of 10 participants
VICTORY_INFLUENCE_THRESHOLD = 9
FAILURE_SUPPORT_THRESHOLD = 2  # Player + 2 others minimum to avoid instant failure
CONSENSUS_THRESHOLD_PERCENT = 0.60  # 60% of participants must be 'Support'
INFLUENCE_THRESHOLD_PERCENT = 0.60  # 60% of *total influence* must come from 'Support'
FAILURE_SUPPORT_THRESHOLD_PERCENT = 0.25  # If 'Support' participants are <= 25%, it's a failure
CRITICAL_CLIMATE_THRESHOLD = 20  # If climate drops <= 20, it's a failure

# Sample names for AI characters
SAMPLE_NAMES = ["Alex", "Ben", "Casey", "Devin", "Erin", "Frankie", "Gabby", "Hayden", "Izzy", "Jamie", "Fatima Ahmed", "David Chen", "Maria Garcia", "Kenji Tanaka", "Chloe Dubois"]

# --- Personality Seed System Data ---
PERSONALITY_TRAITS = {
    'assertiveness': ['low', 'medium', 'high'],
    'risk_tolerance': ['low', 'medium', 'high'],
    'community_orientation': ['individualist', 'balanced', 'collectivist']
}
LIFE_SITUATION_SEEDS = {
    'age_group': ['young (20-35)', 'middle-aged (35-55)', 'older (55+)'],
    'household': ['is single', 'is part of a couple', 'has a family', 'is retired'],
    'occupation': ['a clerical worker', 'a professional', 'a creative', 'a service worker', 'unemployed'],
    'identity_tag': ['has lived here 20 years', 'is a new arrival', 'is a local artist', 'commutes to the city', 'owns a local business']
}
NEGOTIATION_STYLES = ['analytical', 'emotional', 'strategic', 'pragmatic', 'confrontational', 'conciliatory']

# --- Load Game Data from Scenario File ---
def load_scenario_data(filename):
    with open(filename, 'r') as f:
        data = json.load(f)
    return data

SCENARIO_DATA = load_scenario_data(os.path.join('scenarios', 'canadawater.json'))
MASTERPLAN_DATA = load_scenario_data(os.path.join('scenarios', 'masterplan.json')) # Load Masterplan Data for Backend & AI

ROLES = SCENARIO_DATA.get('roles', {})
MICRO_EVENTS = SCENARIO_DATA.get('micro_events', [])
CONTEXT = SCENARIO_DATA.get('context', {})

# --- Onboarding Data (Added from Design Phase) ---
ONBOARDING_DATA = {
    "council_planner": {
        "theme": "light",
        "role_name": "Councilor",
        "description": "You are a publicly elected policymaker, navigating the tension between growth and equity. With pressure from both developers and constituents, your decisions shape the city's long-term direction.",
        "tasks": [
            "Weigh proposals for public interest",
            "Mediate conflicting goals",
            "Cast decisive votes in council meetings"
        ],
        "tokens": 7,
        "portrait": "councilor_portrait.png",
        "mirror_portrait": True,
        "background_style": "organic_shapes"
    },
    "resident_homeowner": {
        "theme": "light",
        "role_name": "Resident",
        "description": "You are part of the existing community with deep ties to the area. You care about identity, affordability, and everyday livability.",
        "tasks": [
            "Express concerns about livability and loss",
            "Assess proposals against neighbourhood values",
            "Propose alternatives that reflect the community's needs",
            "Gain trust from councilors"
        ],
        "tokens": 4,
        "portrait": "resident_portrait.png",
        "mirror_portrait": True,
        "background_style": "plain_white"
    },
    "developer": {
        "theme": "dark",
        "role_name": "Developer",
        "description": "You lead major investments driving Canada Water's redevelopment. Delivering profitable, feasible projects is your priority.",
        "tasks": [
            "Optimize plans for cost and return",
            "Justify profit and trade-offs",
            "Ensure proposals meet requirements",
            "Offer concessions to gain favor"
        ],
        "tokens": 8,
        "portrait": "developer_portrait.png",
        "mirror_portrait": False,
        "background_style": "marble"
    },
    "community_activist": {
        "theme": "light",
        "role_name": "Activist",
        "description": "You advocate for environmental and social well-being. You challenge plans that overlook sustainability or vulnerable groups.",
        "tasks": [
            "Identify environmental or social risks",
            "Mobilize community awareness",
            "Promote fair and sustainable alternatives"
        ],
        "tokens": 4,
        "portrait": "activist_portrait.png",
        "mirror_portrait": False,
        "background_style": "full_texture"
    },
    "potential_buyer": {
        "theme": "light",
        "role_name": "Future Buyer",
        "description": "You seek a home that fits your lifestyle, budget, and long-term plans. Your choices reflect the lived experience of Canada Water.",
        "tasks": [
            "Voice concerns about density and quality of life",
            "Evaluate proposals for comfort and affordability",
            "Support options that ensure stable living conditions"
        ],
        "tokens": 3,
        "portrait": "buyer_portrait.png",
        "mirror_portrait": False,
        "background_style": "organic_shapes_buyer"
    },
    "urban_designer": {
        "theme": "light",
        "role_name": "Architect",
        "description": "As an architect, you design spaces that balance creativity, feasibility, and community needs. Your decisions shape how people live and move in Canada Water.",
        "tasks": [
            "Translate goals into functional designs",
            "Balance the needs of different stakeholders",
            "Collaborate with allies for greater leverage"
        ],
        "tokens": 5,
        "portrait": "architect_portrait.png",
        "mirror_portrait": False,
        "background_style": "architect_texture"
    }
}

@app.route('/onboarding')
def onboarding():
    role_id = session.get('player_role_id')
    if not role_id or role_id not in ONBOARDING_DATA:
        return redirect(url_for('role_selection'))
    
    data = ONBOARDING_DATA[role_id]
    return render_template('onboarding.html', role=data)

@app.route('/customization', methods=['GET', 'POST'])
def customization():
    role_id = session.get('player_role_id')
    if not role_id or role_id not in ONBOARDING_DATA:
        return redirect(url_for('role_selection'))
    
    data = ONBOARDING_DATA[role_id]
    
    if request.method == 'POST':
        # 1. Create Player Profile
        session['player_profile'] = {
            'role_id': role_id,
            'role_name': data['role_name'],
            'portrait': data['portrait'],
            'local_resident': request.form.get('local_resident') == 'on',
            'age': request.form.get('age'),
            'has_children': request.form.get('has_children') == 'on',
            'backstory': request.form.get('backstory'),
            
            # Game Mechanics Data
            'influence_tokens': data['tokens'],
            'max_tokens': 12,
            'stance_score': 50, # Default neutral
            'initial_stance': 'Support', # Default own stance
            'trust_value': 50,
            'influence': INFLUENCE_SCORES.get(role_id, 2),
            'id': 'player_0',
            'is_player': True,
            'name': "You" # Or fetch name if we add that field back
        }
        
        # 2. Generate AI Opponents
        # We need to ensure generate_ai_opponents is available. 
        # If it's defined later in the file, this call works.
        ai_opponents = generate_ai_opponents(role_id)
        
        # Set initial stances for AI
        for opponent in ai_opponents:
            opponent['stance'] = get_stance_category(opponent['stance_score'])
            
        all_characters = ai_opponents + [session['player_profile']]
        random.shuffle(all_characters)
        session['characters'] = all_characters

        # 3. Initialize Negotiation State
        session['negotiation_state'] = {
            'round': 1,
            'history': [],
            'outcome': None,
            'negotiation_climate': 50,
            'issues': {
                'affordable_share': 35,
                'cultural_venue_scale': 'medium',
                'housing_location_mix': 'balanced'
            }
        }
        
        # 4. Start Game
        return redirect(url_for('home_gaming'))

    return render_template('customization.html', role=data)

# ROLES = {
#     "developer": {
#         "name": "Developer",
#         "description": "Represents the company planning the new development project.",
#         "objective": "Maximize profit while meeting minimum regulatory requirements.",
#         "influence": "Significant financial backing, technical expertise.",
#         "stance_distribution": {STANCES["support"]: 8, STANCES["neutral"]: 2},  # 80% support, 20% neutral
#         "initial_influence_tokens": 6,  # Updated
#         "initial_trust": INITIAL_TRUST
#     },
#     "local_resident": {
#         "name": "Local Resident",
#         "description": "Lives in the neighborhood affected by the development.",
#         "objective": "Preserve community character, minimize disruption, ensure fair compensation.",
#         "influence": "Community support, personal stakes.",
#         "stance_distribution": {STANCES["oppose"]: 6, STANCES["neutral"]: 4},  # 60% oppose, 40% neutral
#         "initial_influence_tokens": 2,  # Updated
#         "initial_trust": INITIAL_TRUST
#     },
#     "council_member": {
#         "name": "Council Member",
#         "description": "An elected official responsible for representing constituent interests.",
#         "objective": "Balance development benefits with community impact, uphold regulations.",
#         "influence": "Political network, regulatory power.",
#         "stance_distribution": {STANCES["neutral"]: 7, STANCES["support"]: 2, STANCES["oppose"]: 1},
#         # 70% neutral, 20% support, 10% oppose
#         "initial_influence_tokens": 5,  # Updated
#         "initial_trust": INITIAL_TRUST
#     },
#     "student_representative": {
#         "name": "Student Representative",
#         "description": "Advocates for student housing and campus-related needs.",
#         "objective": "Secure affordable housing options, improve campus accessibility.",
#         "influence": "Represents a large demographic, potential for mobilization.",
#         "stance_distribution": {STANCES["neutral"]: 5, STANCES["support"]: 4, STANCES["oppose"]: 1},
#         # 50% neutral, 40% support, 10% oppose
#         "initial_influence_tokens": 3,  # Updated
#         "initial_trust": INITIAL_TRUST
#     }
# }

# Micro-Story Events
# --- OLD ARCHWAY SCENARIO DATA (COMMENTED OUT) ---
# MICRO_EVENTS = [
#     {
#         "id": "newspaper_scandal",
#         "text": "Islington Daily exposes potential irregularities in the developer's past projects! Public trust wavers.",
#         "effects": {"target": "role", "role_id": "local_resident", "stance_delta": -15, "climate_delta": -5}
#     },
#     {
#         "id": "student_subsidy",
#         "text": "The city government announces a surprise student housing subsidy program, boosting student optimism!",
#         "effects": {"target": "role", "role_id": "student_representative", "stance_delta": +15, "climate_delta": +5}
#     },
#     {
#         "id": "resident_emergency",
#         "text": "A key Local Resident representative has a sudden family emergency and must skip this round's discussion.",
#         "effects": {"target": "role_specific", "role_id": "local_resident", "skip_round": True}
#         # Target one specific resident
#     },
#     {
#         "id": "unexpected_endorsement",
#         "text": "A respected independent urban planning group unexpectedly endorses the project's core ideas!",
#         "effects": {"target": "all", "stance_delta": +10, "climate_delta": +10}
#     },
#     {
#         "id": "developer_concession",
#         "text": "The Developer offers a minor concession regarding green spaces in the plan.",
#         "effects": {"target": "role", "role_id": "developer", "stance_delta": +5, "climate_delta": +5}
#         # Small boost to developer's perceived score by others
#     },
#     {
#         "id": "budget_cuts_rumor",
#         "text": "Rumors circulate about potential city budget cuts impacting infrastructure needed for the project.",
#         "effects": {"target": "all", "stance_delta": -5, "climate_delta": -10}
#     },
# ]


def trigger_and_apply_event(characters, climate_score, current_round):
    """
    Checks if a random event should trigger based on EVENT_PROBABILITY.
    If triggered, selects a random event, applies its effects to characters
    and climate score, and returns the updated state and event text.
    Handles stance clamping (0-100) and skip_round effect.
    """
    event_triggered_info = None
    event_text = None

    if random.random() < EVENT_PROBABILITY:
        chosen_event = random.choice(MICRO_EVENTS)
        event_text = f"**Event Occurred (Round {current_round}):** {chosen_event['text']}"
        effects = chosen_event['effects']
        event_triggered_info = chosen_event  # Store for potential later use/logging
        print(f"--- EVENT TRIGGERED: {chosen_event['id']} ---")  # Server log

        # Apply climate delta
        climate_delta = effects.get('climate_delta', 0)
        if climate_delta != 0:
            original_climate = climate_score
            climate_score = max(0, min(100, climate_score + climate_delta))
            print(f"EVENT: Climate changed by {climate_delta} from {original_climate} to {climate_score}")

        # Identify affected characters
        stance_delta = effects.get('stance_delta', 0)
        target_type = effects.get('target')
        target_role = effects.get('role_id')
        apply_skip = effects.get('skip_round', False)
        affected_chars_for_event = []

        if target_type == 'all':
            affected_chars_for_event = characters
        elif target_type == 'role' and target_role:
            affected_chars_for_event = [char for char in characters if char['role_id'] == target_role]
        elif target_type == 'role_specific' and target_role:
            # Find all eligible characters for the specific role
            eligible_chars = [char for char in characters if char['role_id'] == target_role and not char.get(
                'skipped_round')]  # Avoid affecting already skipped
            if eligible_chars:
                # Pick one randomly from eligible ones
                char_to_affect = random.choice(eligible_chars)
                affected_chars_for_event = [char_to_affect]

        # Apply effects to identified characters
        for char in affected_chars_for_event:
            # Apply stance delta
            if stance_delta != 0:
                original_score = char['stance_score']
                char['stance_score'] = max(0, min(100, original_score + stance_delta))
                # Recalculate stance category after score change
                char['stance'] = get_stance_category(char['stance_score'])
                print(
                    f"EVENT: Stance for {char['name']} ({char['role_id']}) changed by {stance_delta} -> {char['stance_score']} ({char['stance']})")

            # Apply skip_round effect (only applies if target was role_specific)
            if apply_skip and target_type == 'role_specific':
                char['skipped_round'] = True  # Mark character as skipping this round
                print(f"EVENT: {char['name']} ({char['role_id']}) will skip this round due to event.")

    # Return potentially modified characters, climate, and the event message
    return characters, climate_score, event_text, event_triggered_info


# --- Helper function to derive stance category from score --- #
def get_stance_category(score):
    if score <= 39:
        return "Oppose"
    elif score >= 61:
        return "Support"
    else:
        return "Neutral"


@app.route('/')
def home():
    return render_template('landing.html')


@app.route('/chapter_selection')
def chapter_selection():
    return render_template('chapter_selection.html')


@app.route('/chapter/1')
def chapter_introduction():
    return render_template('chapter_introduction.html')


@app.route('/role_selection', methods=['GET', 'POST'])
def role_selection():
    if request.method == 'POST':
        player_role_id = request.form.get('role')
        if player_role_id in ONBOARDING_DATA: # Check against new data source
            session['player_role_id'] = player_role_id
            # Redirect to onboarding page
            return redirect(url_for('onboarding'))
        else:
            # Handle error: invalid role selected
            return redirect(url_for('role_selection'))

    # Clear any previous session data when returning to role selection
    session.pop('player_role_id', None)
    session.pop('player_profile', None)
    return render_template('role_selection.html', roles=ROLES)

# Character Customization Route (Legacy - Redirecting to new flow if hit directly)
@app.route('/customize', methods=['GET', 'POST'])
def character_customization():
    return redirect(url_for('customization'))


@app.route('/test_custom')
def test_custom():
    """Debug route to test customization template directly"""
    dummy_role = {
        "theme": "light",
        "role_name": "Test Councilor",
        "portrait": "councilor_portrait.png",
        "description": "Debug description.",
        "tokens": 99
    }
    return render_template('customization.html', role=dummy_role)

# Negotiation Group Display Route (Kept for potential review, but flow goes to /negotiation)
@app.route('/negotiation_group')
def negotiation_group():
    # This page is now less relevant in the main flow but can be kept for debugging
    # or showing the initial group before the first round starts.
    characters = session.get('characters')
    if not characters:
        return redirect(url_for('role_selection'))  # Need characters setup first

    return render_template('negotiation_group.html', characters=characters)


# --- Negotiation Stage --- #

@app.route('/home')
def home_gaming():
    if 'player_profile' not in session:
         return redirect(url_for('role_selection'))
    
    player = session['player_profile']
    negotiation_state = session.get('negotiation_state', {})
    current_round = negotiation_state.get('round', 1)
    climate_score = negotiation_state.get('negotiation_climate', 50)
    characters = session.get('characters', [])
    
    # Get top 3 stakeholders (excluding player)
    # For MVP, just take the first 3 AI characters
    ai_chars = [c for c in characters if not c.get('is_player')]
    top_stakeholders = ai_chars[:3]

    return render_template('home_gaming.html', 
                           player=player,
                           top_stakeholders=top_stakeholders,
                           current_round=current_round,
                           climate_score=climate_score)

@app.route('/characters')
def characters_profiles():
    if 'player_profile' not in session:
         return redirect(url_for('role_selection'))
    characters = session.get('characters', [])
    return render_template('characters_profiles.html', characters=characters)

@app.route('/negotiation', methods=['GET', 'POST'])
def negotiation():
    # Ensure negotiation has been initialized
    if 'negotiation_state' not in session or 'characters' not in session or 'player_profile' not in session:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
             return jsonify({'status': 'error', 'message': "Session expired. Please reload."}), 403
        flash("Game session not found or incomplete. Please start a new game.", "error")
        return redirect(url_for('role_selection'))

    negotiation_state = session['negotiation_state']
    characters = session.get('characters', [])
    player_profile = session.get('player_profile', None)
    current_round = negotiation_state['round']

    if request.method == 'POST':
        try:
            action = request.form.get('action')  # Check which button was pressed

            if action == 'give_up':
                negotiation_state['outcome'] = 'Player Gave Up'
                negotiation_state['final_round'] = negotiation_state['round']  # Record when they gave up
                flash('You have chosen to end the negotiation.', 'warning')
                session['negotiation_state'] = negotiation_state
                session.modified = True
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                     return jsonify({'status': 'redirect', 'url': url_for('negotiation')})
                return redirect(url_for('negotiation'))

            # If action wasn't 'give_up', assume 'submit_statement'
            player_statement = request.form.get('player_statement', '').strip()
            word_count = len(player_statement.split())

            # --- Check Minimum Word Count --- #
            if not player_statement:  # Handle empty submission separately if needed
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'status': 'error', 'message': "Please enter a statement."}), 400
                flash('Please enter your statement.', 'warning')
                return redirect(url_for('negotiation'))
            elif word_count < MIN_STATEMENT_WORDS:
                msg = f'Your statement must be at least {MIN_STATEMENT_WORDS} words long (currently {word_count}). Please elaborate.'
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'status': 'error', 'message': msg}), 400
                flash(msg, 'error')
                return redirect(url_for('negotiation'))

            # --- Token Cost for Statement --- #
            player_tokens = player_profile.get('influence_tokens', 0)
            if player_tokens < 1:
                msg = 'Not enough Influence Tokens to make a statement.'
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({'status': 'error', 'message': msg}), 400
                flash(msg, 'error')
                return redirect(url_for('negotiation'))
            else:
                # Deduct token cost
                player_profile['influence_tokens'] -= 1
                # Also update the player character in the main list
                for char in characters:
                    if char.get('is_player'):
                        char['influence_tokens'] = player_profile['influence_tokens']
                        break
                print(f"Player statement cost: 1 token. Remaining: {player_profile['influence_tokens']}")

            # --- Proceed with round logic only if submitting and word count is met ---
            player_id = session['player_profile']['id']

            # --- Store previous stance *category* before potential updates --- #
            # Note: We store the *category* derived from the score at the start of the round
            for char in characters:
                if not char.get('is_player'):  # Only for AI characters
                    # Store category based on score *before* AI response potentially changes it
                    char['previous_stance_category'] = get_stance_category(char.get('stance_score', 50))

            if player_statement:
                round_dialogue = {player_id: player_statement}  # Start round with player

                # --- Clear Previous Skip Flags & Trigger/Apply Event --- #
                for char in characters:
                    char.pop('skipped_round', None)  # Remove flag from previous round if set

                climate_score = negotiation_state.get('negotiation_climate', 50)
                # Get current round *before* potential event happens
                current_round = negotiation_state['round']
                characters, climate_score, event_text, _ = trigger_and_apply_event(characters, climate_score, current_round)
                negotiation_state['negotiation_climate'] = climate_score  # Update climate in state
                if event_text:
                    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                         pass # handled in json return
                    else:
                        flash(event_text, 'info')  # Display event message to player

                ai_responses_data = get_ai_responses(characters, negotiation_state.get('history', []),
                                                     player_statement, climate_score, negotiation_state.get('issues', {}))
                round_dialogue.update({ai_id: data['response'] for ai_id, data in ai_responses_data.items()})

                for char in characters:
                    if not char.get('is_player') and char['id'] in ai_responses_data:
                        char['stance_score'] = ai_responses_data[char['id']]['new_score']

                total_score_change = sum(data.get('score_change', 0) for data in ai_responses_data.values())
                ai_count = len(ai_responses_data)
                if ai_count > 0:
                    average_change = total_score_change / ai_count
                    climate_change = round(average_change * 2)
                    negotiation_state['negotiation_climate'] = max(0, min(100, climate_score + climate_change))

                negotiation_state.setdefault('history', []).append(round_dialogue)
                negotiation_state['round'] = current_round + 1

                if negotiation_state['round'] > MAX_ROUNDS:
                    negotiation_state['outcome'] = check_victory(characters, negotiation_state['negotiation_climate'],
                                                                 negotiation_state.get('issues', {}), negotiation_state.get('history', []))

                negotiation_state['issues'] = update_issues_based_on_stances(characters, negotiation_state.get('issues', {}))
                try:
                    requests.post("http://127.0.0.1:5006/apply-issue-update", json=negotiation_state['issues'], timeout=1)
                except Exception as e:
                    print(f"Could not send issue update to visualization: {e}")

                session['negotiation_state'] = negotiation_state
                session['characters'] = characters
                session['player_profile'] = player_profile
                session.modified = True
                
                # --- Return JSON if AJAX request ---
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({
                        'status': 'success',
                        'new_round': negotiation_state['round'],
                        'climate_score': negotiation_state['negotiation_climate'],
                        'history': format_history_as_messages(negotiation_state.get('history', [])),
                        'player_tokens': player_profile.get('influence_tokens', 0),
                        'event_text': event_text
                    })

            return redirect(url_for('negotiation'))

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"ERROR in negotiation route: {e}")
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'status': 'error', 'message': f"Server Error: {str(e)}"}), 500
            flash(f"An error occurred: {e}", "error")
            return redirect(url_for('negotiation'))


    # --- GET Request ---
    regenerate_tokens_for_round(session)

    characters_for_template = []
    previous_stances = session.get('previous_stance', {})
    for char in session.get('characters', []):
        char_copy = char.copy()
        char_copy['previous_stance'] = previous_stances.get(char['id'])
        characters_for_template.append(char_copy)

    session.pop('previous_stance', None)
    
    # Prepare state with formatted history for initial load
    state_for_template = session.get('negotiation_state', {}).copy()
    if 'history' in state_for_template:
        state_for_template['history'] = format_history_as_messages(state_for_template['history'])

    return render_template('round_gaming.html',
                           state=state_for_template,
                           characters=characters_for_template,
                           player_profile=session.get('player_profile', {}),
                           climate_score=session.get('negotiation_state', {}).get('negotiation_climate', 50),
                           max_rounds=MAX_ROUNDS,
                           INFLUENCE_ACTION_COSTS=INFLUENCE_ACTION_COSTS)

@app.route('/negotiation_mvp_demo')
def negotiation_mvp_demo():
    """Demo route with pre-populated data to test the MVP interface."""
    # Create demo data
    from random import choice
    
    demo_characters = []
    for i, (role_id, role_data) in enumerate(ROLES.items()):
        char = {
            'id': f'ai_{i}',
            'role_id': role_id,
            'role_name': role_data['name'],
            'name': choice(SAMPLE_NAMES),
            'backstory': role_data.get('description', ''),
            'initial_stance': choice(['Support', 'Neutral', 'Oppose']),
            'stance': choice(['Support', 'Neutral', 'Oppose']),
            'stance_score': choice([30, 50, 70]),
            'influence': INFLUENCE_SCORES.get(role_id, 2),
            'influence_tokens': role_data.get('initial_influence_tokens', 5),
            'max_tokens': 10,
            'trust_value': 50,
            'age': choice([28, 35, 45, 53, 58]),
            'gender': choice(['Male', 'Female']),
            'local_resident': choice(['Yes', 'No']),
            'has_children': choice(['Yes', 'No']),
            'num_children': choice([0, 1, 2, 3, 4]),
            'marital_status': choice(['Single', 'Married', 'Divorced']),
            'previous_stance_category': 'Neutral',
            'is_player': False
        }
        demo_characters.append(char)
    
    demo_player = {
        'id': 'player_0',
        'role_id': 'developer',
        'role_name': 'Developer',
        'name': 'You',
        'is_player': True,
        'influence_tokens': 8,
        'max_tokens': 12,
        'stance_score': 75
    }
    
    demo_state = {
        'round': 1,
        'history': [],
        'negotiation_climate': 50,
        'outcome': None
    }
    
    return render_template('negotiation_mvp.html',
                           state=demo_state,
                           characters=demo_characters,
                           player_profile=demo_player,
                           climate_score=50,
                           max_rounds=MAX_ROUNDS)

@app.route('/negotiation_mvp', methods=['GET', 'POST'])
def negotiation_mvp():
    """New MVP interface for negotiation with integrated 3D visualization."""
    # Ensure negotiation has been initialized
    if 'negotiation_state' not in session or 'characters' not in session or 'player_profile' not in session:
        flash("Game session not found or incomplete. Please start a new game.", "error")
        return redirect(url_for('role_selection'))

    negotiation_state = session['negotiation_state']
    characters = session.get('characters', [])
    player_profile = session.get('player_profile', None)
    
    # Regenerate tokens for GET requests
    if request.method == 'GET':
        regenerate_tokens_for_round(session)
    
    # Process POST (same logic as regular negotiation)
    if request.method == 'POST':
        # Reuse the existing negotiation logic
        return negotiation()
    
    return render_template('negotiation_mvp.html',
                           state=session.get('negotiation_state', {}),
                           characters=characters,
                           player_profile=player_profile,
                           climate_score=negotiation_state.get('negotiation_climate', 50),
                           max_rounds=MAX_ROUNDS)

@app.route('/api/negotiation/state')
def get_negotiation_state():
    """API endpoint to get current negotiation state as JSON."""
    if 'negotiation_state' not in session:
        return jsonify({'error': 'No active negotiation'}), 404
    
    return jsonify({
        'currentRound': session['negotiation_state'].get('round', 1),
        'stakeholders': session.get('characters', []),
        'playerProfile': session.get('player_profile', {}),
        'climateScore': session['negotiation_state'].get('negotiation_climate', 50),
        'messages': format_history_as_messages(session['negotiation_state'].get('history', [])),
        'issues': session['negotiation_state'].get('issues', {})
    })

def format_history_as_messages(history):
    """Convert dialogue history to message format for frontend."""
    messages = []
    for round_idx, round_dialogue in enumerate(history):
        for char_id, statement in round_dialogue.items():
            is_player = char_id.startswith('player_')
            messages.append({
                'id': f"{round_idx}_{char_id}",
                'sender': 'player' if is_player else 'ai',
                'stakeholderId': None if is_player else char_id,
                'content': statement,
                'timestamp': None  # Could add timestamps if needed
            })
    return messages

@app.route('/profile/<string:char_id>')
def view_profile(char_id):
    """Displays the profile details for a specific character."""
    if 'characters' not in session:
        # Or perhaps return a simple error page
        return "Character data not found in session. Please start a new game.", 404

    character_to_view = None
    for char in session['characters']:
        if char.get('id') == char_id:
            character_to_view = char
            break

    if character_to_view:
        return render_template('profile.html', character=character_to_view)
    else:
        return f"Character with ID '{char_id}' not found.", 404


# --- Core AI Logic --- #

def format_context_for_prompt(context):
    """Formats the scenario context into a readable string for the LLM prompt."""
    prompt = "\n--- Scenario Context ---\n"
    for category, details in context.items():
        prompt += f"{category.replace('_', ' ').title()}:\n"
        for key, value in details.items():
            prompt += f"  - {key.replace('_', ' ').title()}: {value}\n"
    return prompt

def format_history_for_prompt(history, characters_lookup):
    """Formats the dialogue history into a readable string for the LLM prompt."""
    prompt_history = "\nDialogue History:\n"
    if not history:
        return prompt_history + "No discussion yet.\n"

    for i, round_statements in enumerate(history):
        prompt_history += f"--- Round {i + 1} ---\n"
        for char_id, statement in round_statements.items():
            speaker = characters_lookup.get(char_id)
            speaker_name = speaker.get('name', 'Unknown') if speaker else 'Unknown'
            prompt_history += f"{speaker_name}: {statement}\n"
        prompt_history += "---\n"
    return prompt_history


def get_ai_responses(characters, history, player_statement, climate_score, issues):
    """
    Generates responses using the DNA Persona Engine.
    """
    print("\n--- Generating AI Responses (Persona Engine Active) --- ")
    active_ai_characters = [c for c in characters if not c.get('is_player') and not c.get('skipped_round')]

    responses_data = {}
    client = None
    try:
        client = OpenAI()
    except Exception as e:
        print(f"Warning: OpenAI client failed. Error: {e}")

    # Prepare history
    char_lookup = {c['id']: c for c in characters}
    history_text = format_history_for_prompt(history, char_lookup)

    for ai in active_ai_characters:
        # --- 1. PERSONA GENERATION / RETRIEVAL ---
        if 'persona' not in ai:
            print(f"  [System] Generating new DNA for {ai['name']}...")
            ai['persona'] = generate_dna_persona(ai['role_id'], ai['name'])
        
        persona = ai['persona']
        
        # --- 2. DYNAMIC EMOTION CALCULATION ---
        current_score = ai.get('stance_score', 50)
        
        # Attitude description based on score
        if current_score < 35: emotion = "Hostile / Defensive"
        elif current_score < 45: emotion = "Skeptical / Wary"
        elif current_score < 55: emotion = "Neutral / Waiting"
        elif current_score < 70: emotion = "Interested / Constructive"
        else: emotion = "Enthusiastic / Partnering"
        
        # Climate modifier
        if climate_score < 30: emotion += " (Tense Atmosphere)"
        
        # --- 3. PROMPT ENGINEERING (ULTIMATE VERSION) ---
        issues_summary = (
            f"- Affordable Housing: {issues.get('affordable_housing', {}).get('share_percentage', 'N/A')}% share.\n"
            f"- Cultural Venue: {issues.get('cultural_venue', {}).get('scale', 'N/A')} scale.\n"
        )
        
        role_objective = ROLES.get(ai['role_id'], {}).get('objective', 'To participate in the negotiation.')
        
        # Inject Style Details
        style_dna = STYLES.get(persona['style'], {})
        style_desc = style_dna.get('desc', 'Standard')
        style_keywords = ", ".join(style_dna.get('keywords', []))
        style_grammar = style_dna.get('grammar', 'Standard English')

        # Inject Masterplan Context with AI Perception (Simple Logic)
        masterplan_context = "1. **The Map (Spatial Reality)**:\n"
        for plot_id, plot_data in MASTERPLAN_DATA.items():
            if 'description' in plot_data:
                # Simple sentiment based on role (Mock logic for now, can be expanded)
                impact = "Neutral"
                if ai['role_id'] == 'community_activist' and 'luxury' in plot_data.get('ai_tags', []):
                    impact = "Negative (Symbol of Inequality)"
                elif ai['role_id'] == 'developer' and 'luxury' in plot_data.get('ai_tags', []):
                    impact = "Positive (High ROI)"
                
                masterplan_context += f"   - {plot_data['name']}: {plot_data['description']} -> Impact on you: {impact}\n"

        system_prompt = (
            f"[System]\n"
            f"You are interacting in a high-stakes urban planning simulation called 'Ripple Effect'.\n"
            f"Do not break character. Do not be polite unless your character is polite.\n\n"
            f"[Character Profile]\n"
            f"- Role: {ai['name']} ({ROLES.get(ai['role_id'], {}).get('name')})\n"
            f"- Core Objective: {role_objective}\n"
            f"- Backstory: {persona['bio']}\n"
            f"- Deepest Fear (Pain Point): {persona['pain_point']}\n\n"
            f"[Speaking Style Guidelines]\n"
            f"- Description: {style_desc}\n"
            f"- Syntax/Grammar: {style_grammar}\n"
            f"- Key Vocabulary: {style_keywords}\n\n"
            f"[Contextual Awareness]\n"
            f"{masterplan_context}\n"
            f"2. **The Table (Negotiation State)**:\n"
            f"   - Current Deal: {issues_summary.replace(chr(10), ', ')}\n"
            f"   - Current Stance Score: {current_score}/100 ({emotion})\n"
            f"   - Trust Level: {emotion}\n\n"
            f"[Task]\n"
            f"1. **Think First**: Analyze the player's proposal. Is it a distraction? Does it hurt your objective?\n"
            f"2. **Select Strategy**: If trust is low, be skeptical. If high, be collaborative but demanding.\n"
            f"3. **Draft Response**: Use your Style. MUST reference a specific Plot ID if relevant.\n\n"
            f"[Output Format - JSON]\n"
            f"Return a JSON object with keys: 'thought_process', 'dialogue', 'score_delta' (integer -10 to 10), 'animation_trigger' (optional string)."
        )

        if not client:
            # Mock Fallback
            responses_data[ai['id']] = {
                'response': f"[Mock {persona['style']} Voice]: I am a {persona['summary']}. I hear you say '{player_statement}' but my pain point is real.",
                'new_score': current_score,
                'score_change': 0
            }
            continue

        try:
            print(f"  [System] Sending JSON request to OpenAI for {ai['name']}...")
            completion = client.chat.completions.create(
                model="gpt-4o-mini", # Switched to 4o-mini for speed/cost/availability
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Dialogue History:\n{history_text}\n\nPlayer says: \"{player_statement}\""}
                ],
                max_tokens=250,
                temperature=0.9,
                response_format={"type": "json_object"}
            )
            
            ai_response_json = json.loads(completion.choices[0].message.content)
            
            # --- 4. PARSE JSON RESPONSE ---
            ai_dialogue = ai_response_json.get('dialogue', '...')
            thought_process = ai_response_json.get('thought_process', '')
            score_change = int(ai_response_json.get('score_delta', 0))
            
            # Apply sensitivity from global ROLES
            sensitivity = ROLES.get(ai['role_id'], {}).get('ai_response_sensitivity', 1.0)
            score_change = int(score_change * sensitivity)
            
            # Clamp score
            new_score = max(0, min(100, current_score + score_change))
            
            print(f"  -> {ai['name']} Thought: {thought_process}")
            print(f"  -> {ai['name']} Says: \"{ai_dialogue[:50]}...\" (Score: {score_change})")
            
            responses_data[ai['id']] = {
                'response': ai_dialogue,
                'new_score': new_score,
                'score_change': score_change,
                'persona_summary': persona['summary'],
                'thought_process': thought_process # Optional: Store for debugging/display
            }

        except Exception as e:
            print(f"  Error generating response for {ai['name']}: {e}")
            # Return the error as the response so we can see it in the UI
            error_msg = f"[System Error]: {str(e)}"
            responses_data[ai['id']] = {'response': error_msg, 'new_score': current_score, 'score_change': 0}

    return responses_data

# --- Victory Check Logic --- #
def update_issues_based_on_stances(characters, current_issues):
    """Adjusts sub-issues based on the weighted stances and polarization of all characters."""
    net_forces = {
        'affordable_housing_share': 0,
        'cultural_venue_scale': 0
    }

    for char in characters:
        role_id = char['role_id']
        preferences = ROLES.get(role_id, {}).get('issue_preferences', {})
        normalized_stance = (char['stance_score'] - 50) / 50
        polarization_factor = 1 + (char.get('polarization_score', 0) / 100)

        for issue, pref_value in preferences.items():
            if issue in net_forces:
                net_forces[issue] += normalized_stance * pref_value * char['influence'] * polarization_factor

    new_issues = current_issues.copy()

    # --- Safety: Ensure keys exist to prevent KeyError ---
    if 'affordable_housing' not in new_issues:
        new_issues['affordable_housing'] = {'share_percentage': 35, 'type_mix': 'Mixed', 'distribution': 'Even'}
    if 'cultural_venue' not in new_issues:
        new_issues['cultural_venue'] = {'scale': 'medium', 'management_model': 'Community', 'operating_hours': 'Daytime'}

    # Update Affordable Share
    if net_forces['affordable_housing_share'] > 5:
        new_issues['affordable_housing']['share_percentage'] = min(100, new_issues['affordable_housing'].get('share_percentage', 35) + 1)
    elif net_forces['affordable_housing_share'] < -5:
        new_issues['affordable_housing']['share_percentage'] = max(0, new_issues['affordable_housing'].get('share_percentage', 35) - 1)

    # Update Cultural Venue Scale
    scale_map = ['small', 'medium', 'large']
    current_scale_val = new_issues['cultural_venue'].get('scale', 'medium')
    # Safety check if current_scale_val is invalid
    if current_scale_val not in scale_map:
        current_scale_val = 'medium'
    
    current_scale_index = scale_map.index(current_scale_val)
    
    if net_forces['cultural_venue_scale'] > 5 and current_scale_index < 2:
        new_issues['cultural_venue']['scale'] = scale_map[current_scale_index + 1]
    elif net_forces['cultural_venue_scale'] < -5 and current_scale_index > 0:
        new_issues['cultural_venue']['scale'] = scale_map[current_scale_index - 1]

    print(f"--- Issues Updated ---")
    print(f"  Affordable Housing Share: {new_issues['affordable_housing'].get('share_percentage')}% (Force: {net_forces['affordable_housing_share']:.2f})")
    print(f"  Cultural Venue Scale: {new_issues['cultural_venue'].get('scale')} (Force: {net_forces['cultural_venue_scale']:.2f})")

    return new_issues

def regenerate_tokens_for_round(session_data):
    """Regenerates influence tokens for all characters at the start of a round."""
    characters = session_data.get('characters', [])
    player_profile = session_data.get('player_profile', {})
    current_round = session_data.get('negotiation_state', {}).get('round', 1)

    if current_round <= 1: # No regeneration on the first round
        return

    print(f"--- Regenerating Tokens for Round {current_round} ---")
    
    # Check for regen penalty
    regen_penalty = session_data.get('regen_penalty', False)
    if regen_penalty:
        player_regen = 1
        session_data['regen_penalty'] = False # Reset after applying
        print("  Player penalized: +1 token this round.")
    else:
        player_regen = 2
    
    npc_regen = 1

    for char in characters:
        role_id = char['role_id']
        initial_tokens = ROLES.get(role_id, {}).get('initial_influence_tokens', 5)
        max_tokens = int(initial_tokens * 1.5)
        
        current_tokens = char.get('influence_tokens', 0)
        
        if char.get('is_player'):
            new_tokens = min(current_tokens + player_regen, max_tokens)
            char['influence_tokens'] = new_tokens
            if player_profile: player_profile['influence_tokens'] = new_tokens
            print(f"  Player tokens: {current_tokens} + {player_regen} -> {new_tokens} (Max: {max_tokens})")
        else:
            new_tokens = min(current_tokens + npc_regen, max_tokens)
            char['influence_tokens'] = new_tokens

    session_data['characters'] = characters
    session_data['player_profile'] = player_profile

def check_victory(characters, climate_score, issues, history):
    """Determines the outcome based on a more complex set of rules for the Canada Water scenario."""

    # --- Pre-computation of final state ---
    final_stances = {char['id']: get_stance_category(char['stance_score']) for char in characters}
    council_planner_id = next((c['id'] for c in characters if c['role_id'] == 'council_planner'), None)
    council_stance = final_stances.get(council_planner_id, STANCES['neutral'])
    affordable_share = issues.get('affordable_share', 0)
    cultural_venue = issues.get('cultural_venue_scale', 'none')

    # Check for the council policy change event having occurred
    affordable_floor = 35
    for round_history in history:
        if 'event' in round_history and round_history['event']['id'] == 'council_policy_change':
            affordable_floor = 40
            break

    # --- Rule 1: Automatic Failures ---
    if climate_score <= CRITICAL_CLIMATE_THRESHOLD:
        return f"Critical Failure: The negotiation climate collapsed (Climate: {climate_score}). Trust is broken, and no agreement is possible."
    if council_stance == STANCES['oppose']:
        return f"Project Vetoed: The Council Planner refused to approve the plan, leading to an automatic failure."
    if affordable_share < affordable_floor:
        return f"Compliance Failure: The final plan with {affordable_share}% affordable housing fell below the legal minimum of {affordable_floor}%, making it non-compliant."

    # --- Rule 2: Clear Victories ---
    # Developer-centric victory
    developer_win_condition = affordable_share < 40 and cultural_venue in ['small', 'medium']
    if developer_win_condition:
        return f"Developer Victory: The project is highly profitable. A financially-driven plan was approved with {affordable_share}% affordable housing and a '{cultural_venue}' cultural venue."

    # Community-centric victory
    community_win_condition = affordable_share >= 45 and cultural_venue == 'large' and final_stances.get(next((c['id'] for c in characters if c['role_id'] == 'community_activist'), None)) == STANCES['support']
    if community_win_condition:
        return f"Community Victory: A landmark agreement was reached, securing {affordable_share}% affordable housing and a 'large' cultural venue, with strong backing from community advocates."

    # --- Rule 3: Compromise Outcomes (Default) ---
    return f"Compromise Deal: The negotiation ended in a balanced compromise. The final plan includes {affordable_share}% affordable housing and a '{cultural_venue}' scale cultural venue. While not a clear win for any single party, the project moves forward."


def generate_backstory(ai_profile):
    """Generates a natural language backstory from a personality profile."""
    p = ai_profile['personality']
    return (
        f"{ai_profile['name']} is {random.choice(['a', 'an'])} {p['age_group']} {p['occupation']} who {p['identity_tag']} and {p['household']}. "
        f"They have a {p['community_orientation']} worldview with {p['assertiveness']} assertiveness and {p['risk_tolerance']} risk tolerance. "
        f"In discussions, they tend to be {p['negotiation_style']}."
    )

def generate_ai_opponents(player_role_id):
    """Generates a list of AI-controlled opponents based on the roles and multipliers in the scenario file."""
    opponents = []
    opponent_id_counter = 0
    used_names = set()
    multipliers = SCENARIO_DATA.get('multipliers', {})

    for role_id, role_data in ROLES.items():
        if role_id == player_role_id:
            continue

        num_to_create = multipliers.get(role_id, 1)

        for i in range(num_to_create):
            name = random.choice(SAMPLE_NAMES)
            while name in used_names:
                name = random.choice(SAMPLE_NAMES)
            used_names.add(name)

            stance_dist = role_data.get('stance_distribution', {STANCES["neutral"]: 1})
            possible_stances = list(stance_dist.keys())
            weights = list(stance_dist.values())
            chosen_initial_stance = random.choices(possible_stances, weights=weights, k=1)[0]

            chosen_initial_score = {
                STANCES["support"]: INITIAL_SUPPORT_SCORE,
                STANCES["neutral"]: INITIAL_NEUTRAL_SCORE,
                STANCES["oppose"]: INITIAL_OPPOSE_SCORE
            }.get(chosen_initial_stance, INITIAL_NEUTRAL_SCORE)

            ai_profile = {
                'id': f'ai_{opponent_id_counter}',
                'role_id': role_id,
                'role_name': role_data['name'],
                'name': name,
                'is_player': False,
                'influence': INFLUENCE_SCORES.get(role_id, 1),
                'initial_stance': chosen_initial_stance,
                'stance_score': chosen_initial_score,
                'stance': get_stance_category(chosen_initial_score),
                'influence_tokens': role_data['initial_influence_tokens'],
                'max_tokens': 8, # NPC max tokens
                'trust_value': role_data.get('initial_trust', INITIAL_TRUST),
                'age': random.choice([28, 35, 42, 45, 53, 58, 62, 67]),
                'gender': random.choice(['Male', 'Female']),
                'local_resident': random.choice(['Yes', 'No']),
                'has_children': random.choice(['Yes', 'No']),
                'marital_status': random.choice(['Single', 'Married', 'Divorced', 'Widowed']),
                'personality': {
                    'assertiveness': random.choice(PERSONALITY_TRAITS['assertiveness']),
                    'risk_tolerance': random.choice(PERSONALITY_TRAITS['risk_tolerance']),
                    'community_orientation': random.choice(PERSONALITY_TRAITS['community_orientation']),
                    'age_group': random.choice(LIFE_SITUATION_SEEDS['age_group']),
                    'household': random.choice(LIFE_SITUATION_SEEDS['household']),
                    'occupation': random.choice(LIFE_SITUATION_SEEDS['occupation']),
                    'identity_tag': random.choice(LIFE_SITUATION_SEEDS['identity_tag']),
                    'negotiation_style': random.choice(NEGOTIATION_STYLES)
                }
            }
            ai_profile['num_children'] = random.choice([1, 2, 3, 4]) if ai_profile['has_children'] == 'Yes' else 0
            ai_profile['objective'] = role_data.get('objective', 'To influence the outcome.')
            ai_profile['backstory'] = generate_backstory(ai_profile)
            ai_profile['polarization_score'] = 0 # Initialize polarization
            ai_profile['previous_stance_category'] = ai_profile['stance']  # Initialize previous stance
            opponents.append(ai_profile)
            opponent_id_counter += 1

    return opponents


@app.route('/influence', methods=['POST'])
def influence():
    action = request.form.get('action')
    target_id = request.form.get('target_id')
    
    # --- 1. Initialize/Get History ---
    if 'player_action_history' not in session:
        session['player_action_history'] = []
    history = session['player_action_history']

    # --- 2. Find Target ---
    characters = session.get('characters', [])
    target_npc = next((char for char in characters if char['id'] == target_id), None)

    if not target_npc:
        return jsonify({'success': False, 'message': 'Target NPC not found.'}), 404

    target_role_id = target_npc.get('role_id')
    role_data = ROLES.get(target_role_id, {})
    
    # --- 3. Calculate Cost ---
    base_cost = INFLUENCE_ACTION_COSTS.get(action, 0)
    
    # Apply role token modifier
    token_modifiers = role_data.get('token_modifiers', {})
    # Map action names if needed, assuming keys in JSON match action strings (e.g. "gentle", "strong")
    # But action strings are "gentle_persuasion", "strong_persuasion".
    # The JSON keys were "gentle", "strong", "pressure", "recruit".
    # I need to map them.
    key_map = {
        "gentle_persuasion": "gentle",
        "strong_persuasion": "strong",
        "pressure_opponent": "pressure",
        "ally_recruitment": "recruit"
    }
    short_key = key_map.get(action, action)
    modifier = token_modifiers.get(short_key, 1.0)
    
    final_cost = base_cost * modifier
    
    # consecutive pressure penalty
    if action == 'pressure_opponent' and history and history[-1] == 'pressure_opponent':
        final_cost += 2
        
    final_cost = math.ceil(final_cost)

    # --- 4. Check Affordability ---
    player_profile = session.get('player_profile', {})
    if not player_profile:
         return jsonify({'success': False, 'message': 'Player profile not found.'}), 400
         
    if player_profile.get('influence_tokens', 0) < final_cost:
        return jsonify({'success': False, 'message': f'Not enough tokens. Cost: {final_cost}'}), 400

    # --- 5. Apply Penalties (Regen) ---
    # If player uses strong twice in a row
    if action == 'strong_persuasion' and history and history[-1] == 'strong_persuasion':
        session['regen_penalty'] = True
        print("PENALTY: Consecutive strong persuasion triggered regen penalty.")

    # --- 6. Update History ---
    history.append(action)
    if len(history) > 5:
        history.pop(0)
    session['player_action_history'] = history # Save back to session

    # --- 7. Calculate Effects ---
    action_effect = INFLUENCE_ACTION_EFFECTS.get(action, {})
    role_sensitivities = role_data.get('sensitivities', {})
    sensitivity_multiplier = role_sensitivities.get(action, 1.0)

    stance_change = action_effect.get('stance_delta', 0) * sensitivity_multiplier
    trust_change = action_effect.get('trust_delta', 0) * sensitivity_multiplier

    # Polarization & Random Leakage Logic
    leak_occurred = False
    if action == 'pressure_opponent':
        pol_mod = role_data.get('polarization_modifier', 1.0)
        leak_chance = BASE_LEAK_CHANCE * pol_mod
        
        if random.random() < leak_chance:
            leak_occurred = True
            print(f"!!! Pressure LEAKED! Chance: {leak_chance:.2f}. Spreading opposition...")
            
            # Spread impact to others
            for char in characters:
                if not char.get('is_player') and char['id'] != target_id:
                    old_s = char.get('stance_score', 50)
                    char['stance_score'] = max(0, min(100, old_s - POLARIZATION_SPREAD_IMPACT))
                    char['stance'] = get_stance_category(char['stance_score'])
                    print(f"  -> {char['name']} reacted to leak: {old_s} -> {char['stance_score']}")
                    
        # Also update target's polarization score tracking (internal metric)
        target_npc['polarization_score'] = max(0, min(100, target_npc.get('polarization_score', 0) + 10))
        
    elif action == 'gentle_persuasion':
        target_npc['polarization_score'] = max(0, min(100, target_npc.get('polarization_score', 0) - 5))

    # --- 8. Apply Changes ---
    old_stance_score = target_npc.get('stance_score', INITIAL_NEUTRAL_SCORE)
    old_trust = target_npc.get('trust_value', INITIAL_TRUST)

    target_npc['stance_score'] = max(0, min(100, target_npc.get('stance_score', INITIAL_NEUTRAL_SCORE) + stance_change))
    target_npc['trust_value'] = max(0, min(100, target_npc.get('trust_value', INITIAL_TRUST) + trust_change))
    
    new_stance = get_stance_category(target_npc['stance_score'])
    target_npc['stance'] = new_stance

    print(
        f"Applied '{action}' to {target_npc['name']}. Cost: {final_cost}. Stance: {old_stance_score} -> {target_npc['stance_score']} ({new_stance}). Trust: {old_trust} -> {target_npc['trust_value']}")

    # Deduct tokens
    player_profile['influence_tokens'] -= final_cost
    session['player_profile'] = player_profile
    
    # Sync with characters list
    for char in characters:
        if char.get('is_player'):
            char['influence_tokens'] = player_profile['influence_tokens']
            break
    session['characters'] = characters
    
    return jsonify({'success': True, 'message': f'Action applied. Cost: {final_cost}T.'})


# --- 2D Visualization (Ripple Effect) ---

@app.route('/ripple')
def ripple_view():
    """Serves the main page for the 2D visualization and initializes history."""
    if 'history' not in session:
        # On first visit, load the pristine data and set up history
        with open('static/scene.json', 'r') as f:
            original_data = json.load(f)
        session['history'] = {
            'pristine': original_data,
            'undo_stack': [],
            'redo_stack': []
        }
        # The 'current' state is what we'll show and modify
        session['current_scene'] = original_data
    return render_template('ripple.html')


def interpret_command_with_ai(command, client, entities):
    """ Uses an LLM to interpret the user's command into a structured format. """

    # Create a simplified list of entities for the prompt
    entity_list_for_prompt = []
    for entity in entities:
        entity_list_for_prompt.append(
            f"- {entity['id']}: a {entity['type']} with width {entity['params']['width']} and length {entity['params']['length']}"
        )

    system_prompt = f"""
    You are an AI assistant for a 2D architectural planning tool. Your task is to interpret natural language commands and convert them into a structured JSON object.

    The user's plan contains the following entities:
    {chr(10).join(entity_list_for_prompt)}

    You must support three types of actions:
    1. 'change': To change all entities from one layer/type to another.
       - JSON: {{"action": "change", "source": "<source_type>", "destination": "<destination_type>"}}
    2. 'remove': To delete all entities on a specific layer/type.
       - JSON: {{"action": "remove", "layer": "<layer_to_remove>"}}
    3. 'update_params': To modify the parameters of a SINGLE entity, identified by its ID.
       - JSON: {{"action": "update_params", "target_id": "<entity_id>", "params": {{"width": <new_width>, "length": <new_length>}}}}

    Examples:
    - User: "change all hospitals to schools" -> {{"action": "change", "source": "hospital", "destination": "school"}}
    - User: "remove the residential areas" -> {{"action": "remove", "layer": "residential"}}
    - User: "make hotel-0 smaller, 20 by 30" -> {{"action": "update_params", "target_id": "hotel-0", "params": {{"width": 20, "length": 30}}}}
    - User: "reduce the size of btr-2" -> You must ask for specific dimensions.

    IMPORTANT: For 'update_params', you MUST have specific numerical dimensions. If the user is vague (e.g., "make it smaller"), you must ask for clarification by returning a 'clarify' action.
    - Clarification JSON: {{"action": "clarify", "message": "What specific dimensions should I set for [entity_id]?"}}
    """

    response = client.chat.completions.create(
        model="gpt-4-turbo",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": command}
        ],
        response_format={"type": "json_object"}
    )
    return json.loads(response.choices[0].message.content)

@app.route('/update-plan', methods=['POST'])
def update_plan():
    """ Non-destructively updates the plan based on an AI-interpreted command. """ 
    command = request.json.get('command', '')
    if not command:
        return jsonify({'status': 'error', 'message': 'No command provided'}), 400

    try:
                # --- AI Interpretation Step ---
        client = OpenAI()
        interpreted_action = interpret_command_with_ai(command, client, session.get('current_scene', {}).get('entities', []))
        action = interpreted_action.get('action')

        current_scene = session.get('current_scene', {})
        import copy
        new_scene = copy.deepcopy(current_scene)

        history = session['history']
        history['undo_stack'].append(current_scene)
        history['redo_stack'].clear()

        modified = False
        message = ""

        if action == 'change':
            source = interpreted_action.get('source')
            dest = interpreted_action.get('destination')
            for entity in new_scene['entities']:
                if entity['type'] == source:
                    entity['type'] = dest
                    entity['layer'] = dest # Keep layer and type in sync
                    modified = True
            if modified:
                message = f'Changed all "{source}" to "{dest}".'
            else:
                message = f'Layer "{source}" not found.'

        elif action == 'remove':
            layer_to_remove = interpreted_action.get('layer')
            original_count = len(new_scene['entities'])
            new_scene['entities'] = [e for e in new_scene['entities'] if e['layer'] != layer_to_remove]
            if len(new_scene['entities']) < original_count:
                modified = True
                message = f'Removed all entities on layer "{layer_to_remove}".'
            else:
                message = f'Layer "{layer_to_remove}" not found.'

        elif action == 'update_params':
            target_id = interpreted_action.get('target_id')
            new_params = interpreted_action.get('params')
            for entity in new_scene['entities']:
                if entity['id'] == target_id:
                    entity['params'].update(new_params)
                    modified = True
                    break
            if modified:
                message = f'Updated parameters for entity "{target_id}".'
            else:
                message = f'Entity "{target_id}" not found.'
        
        elif action == 'clarify':
            # This is a non-modifying action, just return the AI's message
            return jsonify({'status': 'info', 'message': interpreted_action.get('message')})
        else:
            raise ValueError("AI returned an unknown action.")

        if not modified:
            history['undo_stack'].pop() # Revert history push
            return jsonify({'status': 'info', 'message': message})

        session['current_scene'] = new_scene
        session['history'] = history
        return jsonify({'status': 'success', 'message': message})

    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Could not process command: {str(e)}'}), 500

@app.route('/get-scene', methods=['GET'])
def get_scene():
    """ Returns the current scene data from the session. """
    return jsonify(session.get('current_scene', {}))

@app.route('/history/<action>', methods=['POST'])
def handle_history(action):
    """ Handles undo, redo, and reset actions. """
    history = session.get('history', {})
    if not history:
        return jsonify({'status': 'error', 'message': 'No history available.'}), 400

    if action == 'undo':
        if not history['undo_stack']:
            return jsonify({'status': 'info', 'message': 'Nothing to undo.'})
        # Move current state to redo stack
        history['redo_stack'].append(session['current_scene'])
        # Pop from undo stack to become the new current state
        session['current_scene'] = history['undo_stack'].pop()
        message = 'Undo successful.'
    
    elif action == 'redo':
        if not history['redo_stack']:
            return jsonify({'status': 'info', 'message': 'Nothing to redo.'})
        # Move current state to undo stack
        history['undo_stack'].append(session['current_scene'])
        # Pop from redo stack to become the new current state
        session['current_scene'] = history['redo_stack'].pop()
        message = 'Redo successful.'

    elif action == 'reset':
        # Restore the pristine, original data
        session['current_scene'] = history['pristine']
        history['undo_stack'].clear()
        history['redo_stack'].clear()
        message = 'Plan has been reset to its original state.'

    elif action == 'show_original':
        # This is a temporary view, does not change the history
        return jsonify(history.get('pristine', {}))

    else:
        return jsonify({'status': 'error', 'message': 'Invalid history action.'}), 400

    session['history'] = history
    return jsonify({'status': 'success', 'message': message})

# --- Main Execution ---

@app.after_request
def add_header(response):
    """
    Add headers to force the browser to not cache static files.
    """
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# --- LEGACY P5.JS ROUTES (MOVED TO legacy_method) ---
# @app.route('/api/plan-data')
# def get_plan_data():
#     """Serves the final, processed geometric data as a single JSON object."""
#     final_data_dir = os.path.join('data', 'processed_json', 'final')
#     if not os.path.isdir(final_data_dir):
#         return jsonify({"error": "Final data directory not found."}), 404
#
#     plan_data = {}
#     for filename in os.listdir(final_data_dir):
#         if filename.endswith('.json'):
#             category = filename.replace('.json', '') # e.g., 'buildings'
#             filepath = os.path.join(final_data_dir, filename)
#             try:
#                 with open(filepath, 'r') as f:
#                     plan_data[category] = json.load(f)
#             except (IOError, json.JSONDecodeError) as e:
#                 print(f"Error loading {filename}: {e}")
#                 plan_data[category] = []
#     
#     return jsonify(plan_data)
#
# @app.route('/visualization')
# def visualization_page():
#     """Renders the main p5.js visualization page."""
#     return render_template('visualization.html')

@app.route('/apply-issue-update', methods=['POST'])
def apply_issue_update():
    data = request.json or {}
    socketio.emit('issue_update', data, broadcast=True)
    return jsonify({'ok': True})

# --- New 3D Pipeline API ---

@app.route('/3d/')
def view_3d():
    """Serves the main page for the 3D urban sandbox."""
    return send_from_directory(THREE_JS_DIR, 'index.html')

@app.route('/3d/<path:filename>')
def serve_3d_assets(filename):
    """Serves static assets for the 3D view."""
    return send_from_directory(THREE_JS_DIR, filename)

@app.route('/api/3d/<layer_name>')
def get_3d_layer(layer_name):
    """Serves a specific layer from the cleaned 3D data."""
    valid_layers = ['buildings_3d', 'water', 'greens', 'roads', 'paths', 'open_spaces']
    if layer_name not in valid_layers:
        return jsonify({'error': 'Invalid layer name'}), 404

    # Use absolute path defined at setup
    geojson_path = os.path.join(THREE_DATA_DIR, f"{layer_name}.geojson")
    
    if not os.path.exists(geojson_path):
        return jsonify({'error': 'GeoJSON file not found. Please run the processing script.'}), 404
    
    with open(geojson_path, 'r') as f:
        data = json.load(f)
    return jsonify(data)

@app.route('/api/masterplan')
def get_masterplan_data():
    """Serves the masterplan semantic data (Plot mappings)."""
    # Now served directly from backend memory (Single Source of Truth)
    return jsonify(MASTERPLAN_DATA)

@app.route('/game')
def game():
    """Unified two-column interface for negotiation + visualization."""
    return render_template('integrated_view.html')

if __name__ == '__main__':
    # Make sure to create a .env file with your OPENAI_API_KEY
    # Example: OPENAI_API_KEY='sk-...'    
    app.run(debug=True, port=5006)
