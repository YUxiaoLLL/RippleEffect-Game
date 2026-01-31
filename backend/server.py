import eventlet
eventlet.monkey_patch()

import sys
import os

# --- DEPLOYMENT VERSION CHECK ---
print("### DEPLOY VERSION: v0.1-Fix-Logs-And-Error-Handling (Commit: 4dfef21) ###", flush=True)
# --------------------------------

# Fix for Render/Gunicorn: Ensure backend directory is in sys.path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, render_template, Response, request, jsonify, session, send_from_directory, redirect, url_for, flash, make_response
import uuid
import time
from flask_socketio import join_room
from flask_socketio import SocketIO
from typing import List
import random
import os
import json
import re
import requests
import math
from collections import Counter
from flask_session import Session  # Import Flask-Session
from openai import OpenAI
from dotenv import load_dotenv
from pathlib import Path
from models import SceneState, Block, Action, SceneUpdate
from agents.persona_engine import generate_dna_persona
from agents.persona_data import STYLES, ROLE_SPEECH_CONSTRAINTS, NUMERIC_POLICY, SPATIAL_GROUNDING_EXAMPLES # Import AI Response Engine v1.0 constants
from constraint_layer import ConstraintLayer, state_from_dict, state_to_dict, ConstraintState # Import Constraint Layer
# import ezdxf
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException
from zone_context import load_zone_facts, infer_active_zone_id, compute_issue_tag, zone_context_text, validate_ai_dialogue

# --- Database & Logging Setup (M2) ---
import sqlite3
import datetime
from urllib.parse import urlparse

# M4: Database Path Configuration (Render Persistence)
DB_PATH = os.environ.get('DB_PATH', 'ripple.db')

# Try importing psycopg2 for Postgres (Production)
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False

def get_db_connection():
    """Get database connection (Postgres if DATABASE_URL set, else SQLite)."""
    db_url = os.environ.get('DATABASE_URL')
    
    if db_url and HAS_POSTGRES:
        try:
            conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
            return conn, 'postgres'
        except Exception as e:
            print(f"Postgres connection failed: {e}. Falling back to SQLite.")
            
    # Fallback to SQLite
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn, 'sqlite'

def init_db():
    """Initialize database schema (Events table)."""
    conn, db_type = get_db_connection()
    try:
        if db_type == 'postgres':
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS events (
                        id TEXT PRIMARY KEY,
                        ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        room_id TEXT,
                        player_id TEXT,
                        role TEXT,
                        event_type TEXT,
                        round_index INTEGER,
                        turn_index INTEGER,
                        payload_json TEXT
                    );
                """)
            conn.commit()
        else:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    room_id TEXT,
                    player_id TEXT,
                    role TEXT,
                    event_type TEXT,
                    round_index INTEGER,
                    turn_index INTEGER,
                    payload_json TEXT
                );
            """)
            conn.commit()
            print(f"Initialized SQLite database ({DB_PATH}).")
    except Exception as e:
        print(f"DB Init Error: {e}")
    finally:
        conn.close()

def log_event(room_id, player_id, event_type, payload=None, role=None, round_idx=None, turn_idx=None):
    """Log an event to the database (async-safe wrapper)."""
    try:
        conn, db_type = get_db_connection()
        event_id = str(uuid.uuid4())
        payload_json = json.dumps(payload) if payload else '{}'
        
        if db_type == 'postgres':
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO events (id, room_id, player_id, role, event_type, round_index, turn_index, payload_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (event_id, room_id, player_id, role, event_type, round_idx, turn_idx, payload_json))
            conn.commit()
        else:
            conn.execute("""
                INSERT INTO events (id, room_id, player_id, role, event_type, round_index, turn_index, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (event_id, room_id, player_id, role, event_type, round_idx, turn_idx, payload_json))
            conn.commit()
        conn.close()
    except Exception as e:
        print(f"Logging Error: {e}")

# Initialize DB on startup
init_db()

# --- Load environment variables from .env file
dotenv_path = Path('.') / '.env'  # Explicitly point to .env in current directory
load_dotenv(dotenv_path=dotenv_path)

# --- Debug: Check if API key is loaded --- #
_openai_key = os.environ.get('OPENAI_API_KEY')
print(
    "DEBUG: OPENAI_API_KEY loaded from environ: "
    + ("<missing>" if not _openai_key else f"<set: ...{_openai_key[-4:]}")
)
# --- End Debug --- #


def _is_host_request():
    """
    Helper to check if request allows host privileges (e.g. localhost/admin).
    For production security, we default to False and rely on session hostId.
    """
    return False


# --- Setup ---
# Calculate absolute paths to ensure Flask finds templates/static regardless of where the script is run from
BASE_DIR = os.path.dirname(os.path.abspath(__file__)) # Directory of server.py (backend/)
PROJECT_ROOT = os.path.dirname(BASE_DIR) # Root directory (RippleEffect/)
TEMPLATE_DIR = os.path.join(PROJECT_ROOT, 'frontend', 'templates')
STATIC_DIR = os.path.join(PROJECT_ROOT, 'frontend', 'static')
THREE_JS_DIR = os.path.join(PROJECT_ROOT, 'frontend', 'static', '3d_client')
THREE_DATA_DIR = os.path.join(PROJECT_ROOT, 'frontend', 'static', '3d_data')

def load_scenario_data(path):
    try:
        full_path = path
        if not os.path.isabs(full_path):
            full_path = os.path.join(PROJECT_ROOT, full_path)
        with open(full_path, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


SCENARIO_DATA = load_scenario_data(os.path.join('scenarios', 'canadawater.json'))
if not isinstance(SCENARIO_DATA, dict):
    SCENARIO_DATA = {}

ROLES = (SCENARIO_DATA.get('roles') or {}) if isinstance(SCENARIO_DATA, dict) else {}
if not isinstance(ROLES, dict):
    ROLES = {}

MASTERPLAN_DATA = load_scenario_data(os.path.join('scenarios', 'masterplan.json'))
if not isinstance(MASTERPLAN_DATA, dict):
    MASTERPLAN_DATA = {}

ONBOARDING_DATA = {}
if not ROLES:
    ROLES = {
        'developer': {'name': 'Developer', 'initial_influence_tokens': 8, 'initial_trust': 50},
        'resident_homeowner': {'name': 'Resident', 'initial_influence_tokens': 4, 'initial_trust': 50},
        'resident_social': {'name': 'Resident (Social)', 'initial_influence_tokens': 3, 'initial_trust': 50},
        'potential_buyer': {'name': 'Future Buyer', 'initial_influence_tokens': 3, 'initial_trust': 50},
        'future_buyer': {'name': 'Future Buyer', 'initial_influence_tokens': 3, 'initial_trust': 50},
        'community_activist': {'name': 'Activist', 'initial_influence_tokens': 4, 'initial_trust': 40},
        'council_planner': {'name': 'Councilor', 'initial_influence_tokens': 7, 'initial_trust': 50},
        'urban_designer': {'name': 'Architect', 'initial_influence_tokens': 5, 'initial_trust': 50},
    }

ZONE_FACTS, ZONE_FACT_ZONES = load_zone_facts(BASE_DIR)

print(f"DEBUG: BASE_DIR: {BASE_DIR}")
print(f"DEBUG: PROJECT_ROOT: {PROJECT_ROOT}")
print(f"DEBUG: TEMPLATE_DIR: {TEMPLATE_DIR}")
print(f"DEBUG: STATIC_DIR: {STATIC_DIR}")
print(f"DEBUG: THREE_JS_DIR: {THREE_JS_DIR}")
print(f"DEBUG: Does THREE_JS_DIR exist? {os.path.isdir(THREE_JS_DIR)}")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet', logger=True, engineio_logger=True)
app.secret_key = os.urandom(24)  # More secure secret key

# --- Constraint Layer Setup ---
constraint_layer = ConstraintLayer()

# --- Server-Side Session Configuration ---
app.config['SESSION_TYPE'] = 'filesystem'  # Store session data in files
app.config['SESSION_PERMANENT'] = False  # Session expires when browser closes
app.config['SESSION_USE_SIGNER'] = True  # Encrypt session cookie identifier
app.config['SESSION_FILE_DIR'] = './.flask_session'  # Optional: Specify directory
Session(app)  # Initialize the session extension

@app.before_request
def _log_request_path():
    try:
        if request.path.startswith('/static/'):
            return
    except Exception:
        pass
    print(f"REQ {request.method} {request.path} from {request.remote_addr}")

@app.errorhandler(Exception)
def _log_unhandled_exception(e):
    if isinstance(e, HTTPException):
        return e
    import traceback
    traceback.print_exc()
    return "Internal Server Error", 500

# --- Multiplayer Room State ---
ROOMS = {}  # Global in-memory room storage
MAX_ROOMS_TOTAL = 3 # M1: Limit total rooms for stability

# M1: Global Access Gate
SITE_PASSWORD = os.environ.get('SITE_PASSWORD') or '2026'

@app.before_request
def check_access():
    """M1: Gate all access behind a global PIN."""
    # Allow static resources and specific endpoints
    if request.endpoint in ('login', 'static', 'health_check'):
        return
    if request.path.startswith('/static/'):
        return
        
    # Check session authentication
    if not session.get('authenticated'):
        # For API requests, return 401 instead of redirecting
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Unauthorized', 'redirect': url_for('login')}), 401
            
        return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """M1: Login page for global access."""
    if request.method == 'POST':
        pin = request.form.get('pin')
        # Simple check - in production use constant-time comparison if strict security needed
        if pin == SITE_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('room_gate'))
        else:
            return render_template('login.html', error="Invalid PIN")
    
    return render_template('login.html')

@app.route('/health')
def health_check():
    return jsonify({'status': 'ok'})


@app.route('/chapter_selection')
def chapter_selection():
    return render_template('chapter_selection.html')


@app.route('/chapter/<int:chapter_id>')
def chapter(chapter_id):
    if int(chapter_id) == 1:
        return render_template('chapter_introduction.html')
    return render_template('chapter_selection.html')


@app.route('/role_selection', methods=['GET', 'POST'])
def role_selection():
    if request.method == 'POST':
        return redirect(url_for('room_gate'))
    return render_template('role_selection.html')

def _get_public_room_state(room_id):
    """Return a sanitized view of the room state for clients."""
    room = ROOMS.get(room_id)
    if not room:
        return {}

    game_state = room.get('game_state') or {}
    negotiation_state = game_state.get('negotiation_state') or {}

    players = room.get('players', []) or []
    total_humans = sum(1 for p in players if p.get('id'))
    ready_by = room.get('readyBy') or []
    ready_count = 0
    try:
        ready_count = len(set([str(x) for x in ready_by if x]))
    except Exception:
        ready_count = 0

    try:
        if int(negotiation_state.get('round', 1) or 1) > MAX_ROUNDS:
            _finalize_multiplayer_game_if_needed(game_state)
            negotiation_state = game_state.get('negotiation_state') or {}
    except Exception:
        pass

    public_phase = room.get('phase', 'lobby')
    if public_phase == 'inGame' and total_humans > 0 and ready_count < total_humans:
        public_phase = 'ready'

    if negotiation_state.get('outcome'):
        return {
            'roomId': room_id,
            'phase': public_phase,
            'players': room.get('players', []),
            'hostId': room.get('hostId'),
            'config': room.get('config', {'maxHumans': 4, 'aiCount': 0}),
            'chapterId': room.get('chapterId'),
            'createdAt': room.get('createdAt'),
            'readyCount': ready_count,
            'readyTotal': total_humans,
            'turnOrder': game_state.get('turn_order', []),
            'turnIndex': game_state.get('turn_index', 0),
            'currentSpeaker': game_state.get('current_speaker'),
            'turnDeadlineTs': game_state.get('turn_deadline_ts'),
            'turnRemainingSec': None,
            'turnDurationSec': TURN_DURATION_SECONDS,
            'roundIndex': negotiation_state.get('round', 1),
            'characters': game_state.get('characters', []),
            'stakeholders': game_state.get('characters', []),
            'messages': [],
            'outcome': negotiation_state.get('outcome'),
            'winnerZone': negotiation_state.get('winner_zone'),
            'winnerCounts': negotiation_state.get('winner_counts'),
        }
    result = {
        'roomId': room_id,
        'phase': public_phase,
        'players': room.get('players', []),
        'hostId': room.get('hostId'),
        'config': room.get('config', {'maxHumans': 4, 'aiCount': 0}), # Ensure config is passed
        'chapterId': room.get('chapterId'),
        'createdAt': room.get('createdAt'),
        'readyCount': ready_count,
        'readyTotal': total_humans,
    }
    if room.get('phase') == 'inGame' and game_state:
        result['turnOrder'] = game_state.get('turn_order', [])
        result['turnIndex'] = game_state.get('turn_index', 0)
        result['currentSpeaker'] = game_state.get('current_speaker')
        result['turnDeadlineTs'] = None
        result['roundPhase'] = game_state.get('round_phase')
        result['phaseDeadlineTs'] = game_state.get('phase_deadline_ts')
        result['phaseDurationSec'] = game_state.get('phase_duration_sec')
        try:
            dl = int(game_state.get('phase_deadline_ts') or 0)
            result['phaseRemainingSec'] = max(0, int(math.ceil((dl - int(time.time() * 1000)) / 1000.0))) if dl else None
        except Exception:
            result['phaseRemainingSec'] = None
        result['turnRemainingSec'] = None
        result['turnDurationSec'] = None
        result['roundIndex'] = (game_state.get('negotiation_state') or {}).get('round', 1)
        result['characters'] = game_state.get('characters', [])
        result['stakeholders'] = result['characters'] # Alias for frontend compatibility
        
        # Format history into messages for frontend polling
        negotiation_state = game_state.get('negotiation_state') or {}
        history = negotiation_state.get('history', [])
        history_meta = negotiation_state.get('history_meta', [])
        current_round_dialogue = negotiation_state.get('current_round_dialogue') or {}
        current_round_meta = negotiation_state.get('current_round_meta') or {}
        
        combined_history = history
        combined_meta = history_meta
        if current_round_dialogue:
            combined_history = history + [current_round_dialogue]
            combined_meta = history_meta + [current_round_meta]
            
        result['messages'] = format_history_as_messages(
            combined_history,
            current_user_id=None, # Public view doesn't differentiate "me" vs others in bubble side, handled by frontend ID check
            meta_history=combined_meta,
            characters=game_state.get('characters', [])
        )
    return result

# --- Game Constants ---
STANCES = {
    "support": "Support",
    "oppose": "Oppose",
    "neutral": "Neutral",
    "compromise": "Compromise"
}
INFLUENCE_SCORES = {}
SAMPLE_NAMES = ["Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Quinn", "Avery", "Parker"]
INITIAL_NEUTRAL_SCORE = 50
INITIAL_SUPPORT_SCORE = 75
INITIAL_OPPOSE_SCORE = 25
INFLUENCE_ACTION_COSTS = {
    'gentle_persuasion': 2,
    'pressure_opponent': 5
}
INFLUENCE_ACTION_EFFECTS = {}

def get_stance_category(score):
    try:
        s = int(score)
    except:
        s = 50
    if s >= 70:
        return STANCES['support']
    if s <= 30:
        return STANCES['oppose']
    return STANCES['neutral']

def _compute_influence_cost(action, role_id, action_history=None):
    """
    Calculate influence token cost for an action.
    Base cost comes from INFLUENCE_ACTION_COSTS.
    Could be modified by role traits or history (e.g. repeated actions cost more).
    """
    base_cost = INFLUENCE_ACTION_COSTS.get(action, 0)
    
    # Example Modifier: If pressure is used too often, maybe cost increases?
    # For MVP, we stick to base cost or simple role modifier if data exists.
    role_data = ROLES.get(role_id, {})
    # e.g. role_data.get('influence_cost_modifiers', {}).get(action, 0)
    
    return base_cost

NEUTRAL_SCORE = 50 # Default neutral score
CRITICAL_CLIMATE_THRESHOLD = 30 # Threshold for critical failure

PERSONALITY_TRAITS = {
    'assertiveness': ['Low', 'Medium', 'High'],
    'risk_tolerance': ['Low', 'Medium', 'High'],
    'community_orientation': ['Self-interested', 'Community-minded', 'Balanced']
}

LIFE_SITUATION_SEEDS = {
    'age_group': ['young', 'middle-aged', 'elderly'],
    'household': ['living alone', 'living with family', 'living with friends'],
    'occupation': ['teacher', 'engineer', 'artist', 'retired'],
    'identity_tag': ['local', 'newcomer', 'commuter']
}

NEGOTIATION_STYLES = ['Collaborative', 'Competitive', 'Accommodating']

MAX_ROUNDS = 5  # v0: 5 Rounds for better pacing
MIN_STATEMENT_WORDS = 15  # New constant
EVENT_PROBABILITY = 0.25  # 25% chance of an event each round
TOKEN_REGEN_RATE = 2  # How many influence tokens characters regain each round
INITIAL_TRUST = 50  # Default starting trust value (0-100)
MAX_PLAYER_TOKENS = 12  # Maximum tokens the player can hold
BASE_LEAK_CHANCE = 0.4 # 40% chance for pressure to leak
POLARIZATION_SPREAD_IMPACT = 4 # Impact on others if pressure leaks
TURN_DURATION_SECONDS = 90

PREPARATION_DURATION_SECONDS = 120
REVIEW_DURATION_SECONDS = 60
SUBMISSION_DURATION_SECONDS = 60
TRANSITION_DURATION_SECONDS = 5


def _max_tokens_for_role(role_id, starting_tokens):
    try:
        base = int(starting_tokens or 0)
    except Exception:
        base = 0

    if base <= 0:
        try:
            base = int((ROLES.get(role_id) or {}).get('initial_influence_tokens', 5) or 5)
        except Exception:
            base = 5

    try:
        return max(1, int(base * 1.5))
    except Exception:
        return max(1, base)


def _compute_zone_winner_from_round_meta(round_meta):
    counts = {'A1': 0, 'A2': 0, 'K1': 0}
    if not isinstance(round_meta, dict):
        return 'TIE', counts
    for _, meta in round_meta.items():
        if not isinstance(meta, dict):
            continue
        zid = (meta.get('intent') or meta.get('zone_id') or '')
        zid = str(zid).upper()
        if zid in counts:
            counts[zid] += 1
    max_v = max(counts.values()) if counts else 0
    winners = [z for z, v in counts.items() if v == max_v and v > 0]
    if len(winners) == 1:
        return winners[0], counts
    return 'TIE', counts


def format_history_as_messages(history, current_user_id=None, meta_history=None, characters=None):
    """Convert dialogue history to message format for frontend."""
    messages = []
    meta_history = meta_history or []
    char_lookup = {c.get('id'): c for c in (characters or []) if c.get('id')}
    for round_idx, round_dialogue in enumerate(history):
        for char_id, statement in round_dialogue.items():
            meta_round = meta_history[round_idx] if round_idx < len(meta_history) and isinstance(meta_history[round_idx], dict) else {}
            meta = meta_round.get(char_id, {}) if isinstance(meta_round, dict) else {}

            if current_user_id is not None:
                is_player = (char_id == current_user_id)
            else:
                is_player = char_id.startswith('player_')

            speaker = char_lookup.get(char_id) or {}
            content = statement
            if content is None or (isinstance(content, str) and content.strip() == ''):
                content = 'No submission'
            messages.append({
                'id': f"{round_idx}_{char_id}",
                'sender': 'player' if is_player else 'ai',
                'stakeholderId': char_id, # Always return ID to distinguish players
                'content': content,
                'timestamp': None,
                'zoneId': meta.get('zone_id'),
                'issueTag': meta.get('issue_tag'),
                'speakerRoleId': (meta.get('role_id') or speaker.get('role_id')),
            })
    return messages


def _set_phase_deadline(game_state, duration_seconds):
    if not game_state:
        return
    try:
        duration_seconds = int(duration_seconds or 0)
    except Exception:
        duration_seconds = 0
    if duration_seconds <= 0:
        game_state['phase_deadline_ts'] = None
        game_state['phase_duration_sec'] = None
        return
    game_state['phase_deadline_ts'] = int(time.time() * 1000) + int(duration_seconds * 1000)
    game_state['phase_duration_sec'] = int(duration_seconds)


def _round_phase_duration_seconds(round_phase, round_idx):
    rp = str(round_phase or '').lower()
    if rp == 'preparation':
        return PREPARATION_DURATION_SECONDS
    if rp == 'review':
        return REVIEW_DURATION_SECONDS
    if rp == 'submission':
        return SUBMISSION_DURATION_SECONDS
    if rp == 'transition':
        return TRANSITION_DURATION_SECONDS
    return SUBMISSION_DURATION_SECONDS


def _ensure_submission_defaults(game_state):
    if not game_state:
        return
    negotiation_state = game_state.get('negotiation_state') or {}
    negotiation_state.setdefault('last_intents', {})
    negotiation_state.setdefault('current_round_dialogue', {})
    negotiation_state.setdefault('current_round_meta', {})

    characters = game_state.get('characters') or []
    zid = negotiation_state.get('active_zone_id') or 'A1'

    for c in characters:
        cid = c.get('id')
        if not cid:
            continue
        meta = (negotiation_state.get('current_round_meta') or {}).get(cid)
        if not isinstance(meta, dict) or not (meta.get('intent') or meta.get('zone_id')):
            intent = (negotiation_state.get('last_intents') or {}).get(cid) or zid
            intent = str(intent).upper() if intent else 'A1'
            if intent not in ('A1', 'A2', 'K1'):
                intent = 'A1'
            negotiation_state['last_intents'][cid] = intent
            negotiation_state['current_round_meta'][cid] = {
                'zone_id': intent,
                'issue_tag': compute_issue_tag(intent),
                'role_id': (c or {}).get('role_id'),
                'intent': intent,
            }
        if cid not in (negotiation_state.get('current_round_dialogue') or {}):
            negotiation_state['current_round_dialogue'][cid] = ''

    game_state['negotiation_state'] = negotiation_state
    return


def _auto_fill_ai_submissions(game_state):
    if not game_state:
        return
    negotiation_state = game_state.get('negotiation_state') or {}
    if negotiation_state.get('outcome'):
        return

    def _zone_prior_for_role(role_id):
        rid = str(role_id or '').strip()
        priors = {
            'developer': {'A1': 0.60, 'A2': 0.25, 'K1': 0.15},
            'council_planner': {'A2': 0.45, 'A1': 0.30, 'K1': 0.25},
            'resident_homeowner': {'A2': 0.50, 'A1': 0.20, 'K1': 0.30},
            'resident_social': {'K1': 0.55, 'A2': 0.30, 'A1': 0.15},
            'community_activist': {'K1': 0.50, 'A2': 0.30, 'A1': 0.20},
            'urban_designer': {'A2': 0.50, 'A1': 0.30, 'K1': 0.20},
            'potential_buyer': {'A2': 0.45, 'A1': 0.35, 'K1': 0.20},
        }
        return priors.get(rid) or {'A1': 0.34, 'A2': 0.33, 'K1': 0.33}

    def _choose_ai_intent(ai_char, negotiation_state):
        zones = ['A1', 'A2', 'K1']
        role_id = (ai_char or {}).get('role_id')
        prior = _zone_prior_for_role(role_id)

        current_meta = negotiation_state.get('current_round_meta') or {}
        counts = {'A1': 0, 'A2': 0, 'K1': 0}
        if isinstance(current_meta, dict):
            for _, m in current_meta.items():
                if not isinstance(m, dict):
                    continue
                z = str(m.get('intent') or m.get('zone_id') or '').upper()
                if z in counts:
                    counts[z] += 1

        last_intents = negotiation_state.get('last_intents') or {}
        my_last = str((last_intents.get((ai_char or {}).get('id')) if isinstance(last_intents, dict) else None) or '').upper()

        weights = []
        for z in zones:
            base = float(prior.get(z, 0.0) or 0.0)
            diversity = 1.0 / float(1 + int(counts.get(z, 0) or 0))
            inertia = 1.15 if (my_last == z) else 1.0
            weights.append(max(0.0001, base * diversity * inertia))

        try:
            return random.choices(zones, weights=weights, k=1)[0]
        except Exception:
            return zones[int(random.random() * len(zones))]

    characters = game_state.get('characters') or []
    history = negotiation_state.get('history', []) or []
    issues = negotiation_state.get('issues', {}) or {}
    climate = negotiation_state.get('negotiation_climate', 50)
    zid = negotiation_state.get('active_zone_id') or 'GLOBAL'
    negotiation_state.setdefault('last_intents', {})
    negotiation_state.setdefault('current_round_dialogue', {})
    negotiation_state.setdefault('current_round_meta', {})

    current_round_dialogue = negotiation_state.get('current_round_dialogue') or {}
    prompt_history = history + [current_round_dialogue] if current_round_dialogue else history

    last_text = ''
    if current_round_dialogue:
        try:
            last_key = list(current_round_dialogue.keys())[-1]
            last_text = str(current_round_dialogue.get(last_key) or '')
        except Exception:
            last_text = ''
    elif history:
        last_round = history[-1] or {}
        try:
            last_key = list(last_round.keys())[-1]
            last_text = str(last_round.get(last_key) or '')
        except Exception:
            last_text = ''

    for c in characters:
        if c.get('is_player'):
            continue
        cid = c.get('id')
        if not cid:
            continue
        if cid in (negotiation_state.get('current_round_meta') or {}):
            continue

        chosen_intent = _choose_ai_intent(c, negotiation_state)

        responses = get_ai_responses(
            characters,
            prompt_history,
            last_text,
            climate,
            issues,
            only_ai_id=cid,
            active_zone_id=chosen_intent,
            current_round=negotiation_state.get('round', 1),
        )
        ai_text = (responses.get(cid) or {}).get('response')
        if not ai_text:
            ai_text = '...'

        ai_intent = str(chosen_intent).upper() if chosen_intent else 'A1'
        if ai_intent not in ('A1', 'A2', 'K1'):
            ai_intent = 'A1'

        negotiation_state['last_intents'][cid] = ai_intent
        negotiation_state.setdefault('current_round_dialogue', {})
        negotiation_state['current_round_dialogue'][cid] = ai_text
        negotiation_state['current_round_meta'][cid] = {
            'zone_id': ai_intent,
            'issue_tag': compute_issue_tag(ai_intent),
            'role_id': (c or {}).get('role_id'),
            'intent': ai_intent,
        }
        last_text = ai_text

    game_state['negotiation_state'] = negotiation_state
    return


def _finalize_submission_round(game_state):
    if not game_state:
        return
    negotiation_state = game_state.get('negotiation_state') or {}
    if negotiation_state.get('outcome'):
        return

    _ensure_submission_defaults(game_state)
    negotiation_state = game_state.get('negotiation_state') or {}

    negotiation_state.setdefault('history', []).append(negotiation_state.get('current_round_dialogue', {}) or {})
    negotiation_state.setdefault('history_meta', []).append(negotiation_state.get('current_round_meta', {}) or {})
    negotiation_state['current_round_dialogue'] = {}
    negotiation_state['current_round_meta'] = {}
    negotiation_state['round'] = int(negotiation_state.get('round', 1) or 1) + 1

    _regen_room_tokens(game_state)
    _finalize_multiplayer_game_if_needed(game_state)
    game_state['negotiation_state'] = negotiation_state
    return


def _advance_round_phase(room_id, room):
    if not room:
        return False
    if room.get('phase') != 'inGame':
        return False
    game_state = room.get('game_state') or {}
    negotiation_state = game_state.get('negotiation_state') or {}
    if negotiation_state.get('outcome'):
        game_state['round_phase'] = 'game_over'
        game_state['phase_deadline_ts'] = None
        game_state['phase_duration_sec'] = None
        room['game_state'] = game_state
        return True

    rp = str(game_state.get('round_phase') or 'preparation').lower()
    try:
        round_idx = int(negotiation_state.get('round', 1) or 1)
    except Exception:
        round_idx = 1

    if rp == 'preparation':
        next_rp = 'review' if round_idx > 1 else 'submission'
        game_state['round_phase'] = next_rp
        game_state['submitted_by'] = []
        game_state['submitted_round'] = round_idx
        if next_rp == 'submission':
            _auto_fill_ai_submissions(game_state)
        _set_phase_deadline(game_state, _round_phase_duration_seconds(next_rp, round_idx))
    elif rp == 'review':
        game_state['round_phase'] = 'submission'
        game_state['submitted_by'] = []
        game_state['submitted_round'] = round_idx
        _auto_fill_ai_submissions(game_state)
        _set_phase_deadline(game_state, _round_phase_duration_seconds('submission', round_idx))
    elif rp == 'submission':
        _finalize_submission_round(game_state)
        negotiation_state = game_state.get('negotiation_state') or {}
        if negotiation_state.get('outcome'):
            game_state['round_phase'] = 'game_over'
            game_state['phase_deadline_ts'] = None
            game_state['phase_duration_sec'] = None
        else:
            game_state['round_phase'] = 'transition'
            _set_phase_deadline(game_state, _round_phase_duration_seconds('transition', round_idx))
    elif rp == 'transition':
        negotiation_state = game_state.get('negotiation_state') or {}
        try:
            round_idx = int(negotiation_state.get('round', 1) or 1)
        except Exception:
            round_idx = 1
        next_rp = 'review' if round_idx > 1 else 'submission'
        game_state['round_phase'] = next_rp
        game_state['submitted_by'] = []
        game_state['submitted_round'] = round_idx
        if next_rp == 'submission':
            _auto_fill_ai_submissions(game_state)
        _set_phase_deadline(game_state, _round_phase_duration_seconds(next_rp, round_idx))
    else:
        game_state['round_phase'] = 'submission'
        game_state['submitted_by'] = []
        game_state['submitted_round'] = round_idx
        _auto_fill_ai_submissions(game_state)
        _set_phase_deadline(game_state, _round_phase_duration_seconds('submission', round_idx))

    room['game_state'] = game_state
    return True


def _enforce_phase_timeout(room_id, room):
    if not room or room.get('phase') != 'inGame':
        return False

    players = room.get('players', []) or []
    total_humans = sum(1 for p in players if p.get('id'))
    ready_by = room.get('readyBy') or []
    try:
        ready_count = len(set([str(x) for x in ready_by if x]))
    except Exception:
        ready_count = 0
    if total_humans > 0 and ready_count < total_humans:
        return False

    game_state = room.get('game_state') or {}
    negotiation_state = game_state.get('negotiation_state') or {}
    if negotiation_state.get('outcome'):
        return False

    deadline = game_state.get('phase_deadline_ts')
    if not deadline:
        rp = str(game_state.get('round_phase') or 'preparation').lower()
        try:
            round_idx = int(negotiation_state.get('round', 1) or 1)
        except Exception:
            round_idx = 1
        if rp == 'submission':
            _auto_fill_ai_submissions(game_state)
        _set_phase_deadline(game_state, _round_phase_duration_seconds(rp, round_idx))
        room['game_state'] = game_state
        return True

    changed = False
    while True:
        try:
            now_ms = int(time.time() * 1000)
            dl = int(game_state.get('phase_deadline_ts') or 0)
        except Exception:
            break
        if not dl or now_ms < dl:
            break
        _advance_round_phase(room_id, room)
        game_state = room.get('game_state') or {}
        negotiation_state = game_state.get('negotiation_state') or {}
        changed = True
        if negotiation_state.get('outcome'):
            break

    room['game_state'] = game_state
    return bool(changed)

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


def get_ai_responses(characters, history, player_statement, climate_score, issues, only_ai_id=None, active_zone_id=None, current_round=None):
    """
    Generates responses using the DNA Persona Engine.
    """
    print("\n--- Generating AI Responses (Persona Engine Active) --- ")

    def _try_parse_json_object(text):
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            pass
        try:
            start = text.find('{')
            end = text.rfind('}')
            if start == -1 or end == -1 or end <= start:
                return None
            return json.loads(text[start:end + 1])
        except Exception:
            return None

    active_ai_characters = [c for c in characters if not c.get('is_player') and not c.get('skipped_round')]
    if only_ai_id:
        active_ai_characters = [c for c in active_ai_characters if c.get('id') == only_ai_id]

    responses_data = {}
    client = None
    try:
        client = OpenAI()
    except Exception as e:
        print(f"Warning: OpenAI client failed. Error: {e}")

    # Prepare history
    char_lookup = {c['id']: c for c in characters}
    history_text = format_history_for_prompt(history, char_lookup)

    zid = (active_zone_id or infer_active_zone_id(player_statement, 'GLOBAL') or 'GLOBAL').upper()
    zone_info, zone_ctx_text = zone_context_text(zid, ZONE_FACT_ZONES)
    issue_tag = compute_issue_tag(zid)
    must_keywords = (zone_info or {}).get('must_mention_keywords') or []

    round_no = 1
    try:
        if current_round is not None:
            round_no = int(current_round)
    except Exception:
        round_no = 1

    zone_prompts = (zone_info or {}).get('discussion_prompts') or []
    if zone_prompts:
        round_prompt = zone_prompts[(max(round_no, 1) - 1) % len(zone_prompts)]
    else:
        round_prompt = "Discuss which zone should start first (A1/A2/K1), based on your role priorities, lived experience, and what you want the future to feel like."

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
        
        # --- 3. PROMPT ENGINEERING (ULTIMATE VERSION v1.0) ---
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

        # Inject Masterplan Context with AI Perception
        masterplan_context = "1. **The Map (Spatial Reality)**:\n"
        for plot_id, plot_data in MASTERPLAN_DATA.items():
            if 'description' in plot_data:
                # Simple sentiment based on role
                impact = "Neutral"
                if ai['role_id'] == 'community_activist' and 'luxury' in plot_data.get('ai_tags', []):
                    impact = "Negative (Symbol of Inequality)"
                elif ai['role_id'] == 'developer' and 'luxury' in plot_data.get('ai_tags', []):
                    impact = "Positive (High ROI)"
                
                masterplan_context += f"   - {plot_data['name']}: {plot_data['description']} -> Impact on you: {impact}\n"

        stance_lines = (STANCE_MATRIX.get(ai.get('role_id')) or {}).get(zid, [])
        stance_guidance = " ".join([s for s in stance_lines if s])

        # --- NEW: Round 1 Logic (Role x Speech Depth) ---
        speech_constraints = ""
        if round_no == 1:
            constraints = ROLE_SPEECH_CONSTRAINTS.get(ai['role_id'], {})
            allowed_topics = ", ".join(constraints.get('allowed', []))
            forbidden_topics = ", ".join(constraints.get('forbidden', []))
            
            speech_constraints = (
                f"[Round 1 Rules - STRICT]\n"
                f"1. **NO Numeric Dumping**: Avoid hard numbers (like '118,000', '79', '£18m') unless it is truly necessary to justify a choice.\n"
                f"2. **Role Depth**: Speak from your identity, lived experience, emotions, and future vision. Use these themes: {allowed_topics}.\n"
                f"3. **FORBIDDEN topics**: {forbidden_topics}.\n"
                f"4. **Goal**: Argue which zone should start first (A1/A2/K1). Surface concerns, signal red lines, and probe others' priorities.\n"
            )
        else:
             speech_constraints = (
                f"[Round {round_no} Guidance]\n"
                f"You can be more specific if trust allows. Use numbers only when they strengthen a concrete argument about the starting sequence.\n"
            )

        # --- NEW: Spatial Grounding (Mandatory) ---
        spatial_examples = ", ".join(random.sample(SPATIAL_GROUNDING_EXAMPLES, min(3, len(SPATIAL_GROUNDING_EXAMPLES))))
        spatial_instruction = (
            f"**Spatial Grounding (MANDATORY)**: You must refer to the physical reality of the site at least once in your response.\n"
            f"   (e.g., mention construction noise, views, shadows, walking paths, specific buildings). Examples: {spatial_examples}.\n"
        )

        sequence_instruction = (
            "**Construction Sequence Focus (MANDATORY)**: Keep your response about which zone should start first (A1/A2/K1). "
            "State your preferred first zone, explain why from your role perspective (feelings, daily impact, hopes), "
            "and name one condition or reassurance you need. Only mention hard numbers if essential." 
        )

        # --- NEW: 50/50 Conflict Dimension Logic ---
        # 50% chance to respond to previous speaker directly, 50% to pivot to a new conflict dimension
        pivot_instruction = ""
        if random.random() < 0.5:
             pivot_instruction = "**Strategy**: Directly respond to the previous speaker's point. Agree, disagree, or qualify it based on your interests."
        else:
             pivot_instruction = "**Strategy**: Acknowledge the previous point briefly, then **PIVOT** to a new, specific conflict dimension relevant to your role (e.g., 'That's fine, but who pays for the maintenance?', 'If we do that, what happens to the park?')."

        system_prompt = (
            f"[System]\n"
            f"You are a player in 'Ripple Effect', a high-stakes urban negotiation game. \n"
            f"**IMPORTANT**: You are NOT a policy writer, a lawyer, or a checklist machine. You are a HUMAN character with specific interests and fears.\n"
            f"Your goal is to win influence and protect your interests through negotiation, pressure, and alliances.\n\n"
            
            f"[Character Profile]\n"
            f"- Role: {ai['name']} ({ROLES.get(ai['role_id'], {}).get('name')})\n"
            f"- Core Objective: {role_objective}\n"
            f"- Backstory: {persona['bio']}\n"
            f"- Deepest Fear (Pain Point): {persona['pain_point']}\n\n"
            
            f"{speech_constraints}\n\n"
            
            f"[Speaking Style Guidelines]\n"
            f"- Voice: {style_desc}\n"
            f"- Keywords: {style_keywords}\n"
            f"- Tone: {emotion}\n"
            f"- Do NOT use templated phrases like 'I understand your point' or 'Let's consider'. Speak naturally.\n\n"
            
            f"[Contextual Awareness]\n"
            f"{masterplan_context}\n"
            f"Current Deal Status: {issues_summary.replace(chr(10), ', ')}\n"
            f"Role Stance on {zid}: {stance_guidance}\n\n"
            
            f"[Task]\n"
            f"1. {pivot_instruction}\n"
            f"2. {spatial_instruction}\n"
            f"3. {sequence_instruction}\n"
            f"4. Keep it short (under 50 words). conversational, and 'human'.\n"
            f"5. **NO LISTS**. Do not use bullet points. Speak in full sentences.\n\n"
            
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
            attempt = 0
            last_errors = []
            ai_dialogue = '...'
            thought_process = ''
            score_change = 0
            ai_response_json = {}  # Initialize with empty dict to prevent UnboundLocalError/AttributeError

            while attempt < 2:
                print(f"  [System] Sending JSON request to OpenAI for {ai['name']}... (attempt {attempt + 1})")
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Dialogue History:\n{history_text}\n\nPlayer says: \"{player_statement}\""},
                ]
                if attempt == 1 and last_errors:
                    messages.append({
                        "role": "user",
                        "content": (
                            "Rewrite your response to satisfy the Round Focus guidance. "
                            f"Fix these failures: {', '.join(last_errors)}. "
                            "Keep it about which zone should start first. "
                            "Avoid hard numbers unless necessary."
                        )
                    })

                # Attempt 1: Try with the requested advanced model (gpt-5-mini)
                try:
                    completion = client.chat.completions.create(
                        model="gpt-5-mini",
                        messages=messages,
                        max_completion_tokens=2500,  # Increased for reasoning models
                        # response_format={"type": "json_object"} # Removed: Not supported by some reasoning models
                    )
                    choice = completion.choices[0]
                    print(f"  [Debug] gpt-5-mini finish_reason: {choice.finish_reason}, refusal: {getattr(choice.message, 'refusal', 'None')}")
                    raw_content = (choice.message.content or '').strip()
                except Exception as e:
                    print(f"  [Warning] gpt-5-mini failed ({e}). Falling back to gpt-4o-mini...")
                    raw_content = ''

                # Fallback: If empty or failed, try standard model
                if not raw_content:
                    print("  [Warning] Primary model returned empty content. Trying fallback...")
                    try:
                        completion = client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=messages,
                            max_tokens=1000,
                            response_format={"type": "json_object"}
                        )
                        raw_content = (completion.choices[0].message.content or '').strip()
                    except Exception as e_fallback:
                        print(f"  [Error] Fallback model also failed: {e_fallback}")

                ai_response_json = _try_parse_json_object(raw_content)
                if not isinstance(ai_response_json, dict):
                    print(f"  [Error] Invalid JSON from {ai['name']}. Raw content:\n'{raw_content}'")
                    # Instead of raising immediately, treat as error for retry
                    last_errors = ["Returned invalid JSON"]
                    attempt += 1
                    continue

                # --- 4. PARSE JSON RESPONSE ---
                ai_dialogue = ai_response_json.get('dialogue', '...')
                thought_process = ai_response_json.get('thought_process', '')
                score_change = int(ai_response_json.get('score_delta', 0))

                last_errors = validate_ai_dialogue(
                    dialogue=ai_dialogue,
                    role_id=ai.get('role_id'),
                    zone_id=zid,
                    zone_info=zone_info,
                    role_voice_keywords=ROLE_VOICE_KEYWORDS,
                    require_zone_id=False,
                    require_zone_fact=False,
                    require_role_voice=False,
                    require_question_by_role=False,
                    allow_other_zones=True,
                )
                if not last_errors:
                    break

                attempt += 1

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
            responses_data[ai['id']] = {'response': '...', 'new_score': current_score, 'score_change': 0}

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
    constraint_regen_penalty = session_data.get('token_regen_delta', 0) # From Constraint Layer

    if regen_penalty:
        player_regen = 1
        session_data['regen_penalty'] = False # Reset after applying
        print("  Player penalized: +1 token this round (Action Penalty).")
    else:
        player_regen = 2
    
    # Apply Constraint Layer Penalty (additive)
    # constraint_regen_penalty is usually negative, e.g., -1
    player_regen += constraint_regen_penalty
    if constraint_regen_penalty != 0:
        print(f"  Player penalized by Constraint Layer: {constraint_regen_penalty} tokens.")
        # Reset constraint penalty (assuming it's per-round impact or persisted in constraint state if permanent)
        # For now, we reset the delta stored in session, as the constraint layer re-applies it if condition persists
        session_data.pop('token_regen_delta', None)

    player_regen = max(0, player_regen) # Ensure non-negative logic

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
                'starting_influence_tokens': role_data['initial_influence_tokens'],
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

    # Attempt 1: gpt-5-mini
    try:
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": command}
            ],
            max_completion_tokens=1000,
            # response_format={"type": "json_object"} # Removed for compatibility
        )
        content = response.choices[0].message.content
    except Exception as e:
        print(f"  [Warning] interpret_command gpt-5-mini failed ({e}). Falling back...")
        content = None

    # Fallback: gpt-4o-mini
    if not content:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": command}
            ],
            max_tokens=1000,
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content

    return json.loads(content)

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
    # Reload from disk to ensure freshness (Development Mode)
    # This prevents stale data if the JSON is edited while server runs
    try:
        data = load_scenario_data(os.path.join('scenarios', 'masterplan.json'))
        return jsonify(data)
    except Exception as e:
        print(f"Error reloading masterplan: {e}")
        # Fallback to the globally loaded one if disk read fails
        return jsonify(MASTERPLAN_DATA)

@app.route('/game')
def game():
    """Unified two-column interface for negotiation + visualization."""
    return render_template('integrated_view.html')


@app.route('/negotiation')
def negotiation():
    """Serves the negotiation interface."""
    return render_template('negotiation_mvp.html')


@app.route('/visualization')
def visualization():
    """Serves the visualization interface."""
    return ripple_view()



# --- Multiplayer Room Routes ---

import socket

def get_local_ip():
    try:
        # Connect to an external server (doesn't send data) to get the interface IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return "127.0.0.1"

@app.route('/room_gate')
def room_gate():
    host_ip = get_local_ip()
    return render_template('room_gate.html', host_ip=host_ip, port=5006)


@app.route('/lobby/<room_id>')
def lobby(room_id):
    room_id = (room_id or '').upper()
    if not room_id or room_id not in ROOMS:
        flash('Room not found.', 'error')
        return redirect(url_for('room_gate'))
    return render_template('lobby.html', room_id=room_id, player_id=session.get('player_id'))


@app.route('/api/config')
def api_config():
    # M1: Everyone can be a host in cloud version (create room = host)
    return jsonify({'isHost': True})


@app.route('/api/rooms/active')
def list_active_rooms():
    rooms = []
    try:
        for rid, room in (ROOMS or {}).items():
            if not room:
                continue
            phase = room.get('phase', 'lobby')
            config = room.get('config') or {}
            max_humans = int(config.get('maxHumans', 4) or 4)
            players = room.get('players', []) or []
            current_humans = len(players)

            if phase == 'lobby':
                status = 'Waiting'
            elif phase == 'inGame':
                status = 'In Progress'
            else:
                status = 'Waiting'

            is_full = current_humans >= max_humans

            rooms.append({
                'roomId': rid,
                'status': 'Full' if is_full else status,
                'phase': phase,
                'currentPlayers': current_humans,
                'maxPlayers': max_humans,
                'createdAt': room.get('createdAt'),
            })
    except Exception:
        rooms = []

    try:
        rooms.sort(key=lambda r: (0 if r.get('status') == 'Waiting' else 1, 0 if r.get('status') == 'In Progress' else 1, -(r.get('currentPlayers') or 0), -(r.get('createdAt') or 0)))
    except Exception:
        pass

    return jsonify({'rooms': rooms})


@app.route('/api/rooms', methods=['POST'])
def create_room():
    # Auto-cleanup stale rooms (> 2 hours)
    now = time.time()
    stale_ids = [rid for rid, r in ROOMS.items() if now - r.get('createdAt', 0) > 7200]
    for rid in stale_ids:
        if rid in ROOMS:
            del ROOMS[rid]
            print(f"Auto-cleaned stale room: {rid}")

    # M1: Limit total rooms
    if len(ROOMS) >= MAX_ROOMS_TOTAL:
        return jsonify({'error': 'Server at capacity (max rooms reached).'}), 503

    payload = request.get_json(silent=True) or {}
    
    # Fix: Handle both flat and nested config structures
    if 'maxHumans' in payload or 'aiCount' in payload:
        config = payload
        host_id = payload.get('hostId') # Might be None, but handled in join/start
    else:
        config = payload.get('config', {})
        host_id = payload.get('hostId')
    
    # Generate short readable room ID (6 chars)
    room_id = uuid.uuid4().hex[:6].upper()
    
    ROOMS[room_id] = {
        'id': room_id,
        'createdAt': time.time(),
        'hostId': host_id,
        'players': [],
        'phase': 'lobby',
        'config': config,
        'game_state': {} # Stores turn order, history, etc.
    }
    
    # M2: Log Event
    log_event(room_id, host_id, 'ROOM_CREATED', payload=payload)
    
    return jsonify({'roomId': room_id})


@app.route('/version')
def get_version():
    """Debug endpoint to verify deployment version."""
    return jsonify({
        "version": "v0.1-Fix-Logs-And-Error-Handling",
        "timestamp": time.time(),
        "message": "If you see this, the new code is running!"
    })


@app.route('/api/admin/reset_rooms', methods=['POST'])
def admin_reset_rooms():
    """Admin: Force clear all rooms. Protected by PIN."""
    payload = request.get_json(silent=True) or {}
    pin = payload.get('pin')
    
    # SITE_PASSWORD is loaded from env or defaults to '2026'
    if str(pin) != str(SITE_PASSWORD):
        return jsonify({'error': 'Invalid PIN'}), 403
        
    count = len(ROOMS)
    ROOMS.clear()
    print(f"ADMIN: Cleared {count} rooms.")
    return jsonify({'ok': True, 'message': f'Cleared {count} rooms.'})


@app.route('/api/rooms/<room_id>/join', methods=['POST'])
def join_room_api(room_id):
    try:
        room_id = (room_id or '').upper()
        room = ROOMS.get(room_id)
        if not room:
            return jsonify({'ok': False, 'error': 'Room not found.'}), 404

        if room.get('phase') != 'lobby':
            return jsonify({'ok': False, 'error': 'Room already started.'}), 400

        payload = request.get_json(silent=True) or {}
        player_name = (payload.get('playerName') or '').strip() or 'Player'

        if len(room.get('players', [])) >= int(room.get('config', {}).get('maxHumans', 4)):
            return jsonify({'ok': False, 'error': 'Room is full.'}), 400

        player_id = payload.get('playerId') or session.get('player_id')
        if player_id:
            existing = next((p for p in room['players'] if p.get('id') == player_id), None)
            if existing:
                # M3: Re-hydrate session for persistent identity
                session['room_id'] = room_id
                session['player_id'] = player_id
                
                # M3: Ensure hostId isn't lost if this was the host rejoining
                if room.get('hostId') == player_id:
                     pass
                     
                return jsonify({'ok': True, 'playerId': player_id})

        if not player_id:
            player_id = f"player_{uuid.uuid4().hex[:8]}"
        
        session['player_id'] = player_id
        session['room_id'] = room_id

        room['players'].append({'id': player_id, 'name': player_name, 'role': None})
        
        # Assign host to the first player if not set
        if room.get('hostId') is None:
            room['hostId'] = player_id

        # M2: Log Event
        log_event(room_id, player_id, 'PLAYER_JOINED', payload={'name': player_name})

        try:
            socketio.emit('room_update', _get_public_room_state(room_id), room=room_id)
        except Exception as e:
            print(f"Socket emit error in join_room: {e}")

        return jsonify({'ok': True, 'playerId': player_id})
    except Exception as e:
        print(f"CRITICAL ERROR in join_room_api: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'ok': False, 'error': 'Internal Server Error'}), 500


@app.route('/api/rooms/<room_id>/state')
def get_room_state(room_id):
    room_id = (room_id or '').upper()
    if room_id not in ROOMS:
        return jsonify({'error': 'Room not found.'}), 404
    room = ROOMS.get(room_id)
    _enforce_phase_timeout(room_id, room)
    return jsonify(_get_public_room_state(room_id))


@app.route('/api/negotiation/state')
def get_negotiation_state():
    """Compatibility API endpoint used by negotiation_mvp.html polling."""
    room_id = session.get('room_id')
    if room_id and room_id in ROOMS:
        room = ROOMS[room_id]
        if room.get('phase') == 'inGame':
            _enforce_phase_timeout(room_id, room)
            game_state = room.get('game_state') or {}
            negotiation_state = game_state.get('negotiation_state') or {}

            players = room.get('players', []) or []
            total_humans = sum(1 for p in players if p.get('id'))
            ready_by = room.get('readyBy') or []
            try:
                ready_count = len(set([str(x) for x in ready_by if x]))
            except Exception:
                ready_count = 0

            public_phase = str(game_state.get('round_phase') or 'preparation')
            if total_humans > 0 and ready_count < total_humans:
                public_phase = 'ready'

            characters = game_state.get('characters') or []
            player_id = session.get('player_id')
            player_profile = next((c for c in characters if c.get('id') == player_id), {})
            is_host = (room.get('hostId') == player_id) or _is_host_request()

            history = negotiation_state.get('history', []) or []
            history_meta = negotiation_state.get('history_meta', []) or []
            current_round_dialogue = negotiation_state.get('current_round_dialogue') or {}
            current_round_meta = negotiation_state.get('current_round_meta') or {}
            combined_history = history
            combined_meta = history_meta
            if current_round_dialogue:
                combined_history = history + [current_round_dialogue]
                combined_meta = history_meta + [current_round_meta]

            active_zone_id = negotiation_state.get('active_zone_id') or 'GLOBAL'
            active_issue_tag = negotiation_state.get('active_issue_tag') or compute_issue_tag(active_zone_id)

            player_role_id = (player_profile or {}).get('role_id')
            action_history = (negotiation_state.get('player_action_history') or {}).get(player_id, [])
            influence_costs = {
                'gentle_persuasion': _compute_influence_cost('gentle_persuasion', player_role_id, action_history=action_history) if player_role_id else INFLUENCE_ACTION_COSTS.get('gentle_persuasion'),
                'pressure_opponent': _compute_influence_cost('pressure_opponent', player_role_id, action_history=action_history) if player_role_id else INFLUENCE_ACTION_COSTS.get('pressure_opponent'),
            }

            dl = game_state.get('phase_deadline_ts')
            try:
                phase_remaining = max(0, int(math.ceil(((int(dl or 0)) - int(time.time() * 1000)) / 1000.0))) if dl else None
            except Exception:
                phase_remaining = None

            submitted_by = game_state.get('submitted_by') or []
            has_submitted = bool(player_id and (player_id in submitted_by))

            include_phase_timer = (public_phase != 'ready')

            return jsonify({
                'currentRound': negotiation_state.get('round', 1),
                'stakeholders': characters,
                'playerProfile': player_profile,
                'playerTokens': (player_profile or {}).get('influence_tokens', 0),
                'influenceCosts': influence_costs,
                'climateScore': negotiation_state.get('negotiation_climate', 50),
                'messages': format_history_as_messages(
                    combined_history,
                    current_user_id=player_id,
                    meta_history=combined_meta,
                    characters=characters,
                ),
                'issues': negotiation_state.get('issues', {}),
                'constraintState': negotiation_state.get('constraint_state', {}),
                'constraintLast': negotiation_state.get('constraint_last', {}),
                'isMultiplayer': True,
                'roomId': room_id,
                'myPlayerId': player_id,
                'isHost': is_host,
                'phase': public_phase,
                'readyCount': ready_count,
                'readyTotal': total_humans,
                'iAmReady': bool(player_id and (str(player_id) in set([str(x) for x in ready_by if x]))),
                'turnOrder': game_state.get('turn_order', []),
                'turnIndex': game_state.get('turn_index', 0),
                'currentSpeaker': game_state.get('current_speaker'),
                'turnDeadlineTs': None,
                'turnRemainingSec': None,
                'turnDurationSec': None,
                'roundPhase': game_state.get('round_phase'),
                'phaseDeadlineTs': (dl if include_phase_timer else None),
                'phaseRemainingSec': (phase_remaining if include_phase_timer else None),
                'phaseDurationSec': (game_state.get('phase_duration_sec') if include_phase_timer else None),
                'hasSubmitted': has_submitted,
                'activeZoneId': active_zone_id,
                'activeIssueTag': active_issue_tag,
                'activeZoneFacts': (ZONE_FACT_ZONES.get(active_zone_id) or ZONE_FACT_ZONES.get('GLOBAL') or {}),
                'outcome': negotiation_state.get('outcome'),
                'winnerZone': negotiation_state.get('winner_zone'),
                'winnerCounts': negotiation_state.get('winner_counts'),
                'lastIntents': negotiation_state.get('last_intents', {}) or {},
            })

    if 'negotiation_state' not in session:
        return jsonify({'error': 'No active negotiation'}), 404

    active_zone_id = session['negotiation_state'].get('active_zone_id') or 'GLOBAL'
    active_issue_tag = session['negotiation_state'].get('active_issue_tag') or compute_issue_tag(active_zone_id)
    return jsonify({
        'currentRound': session['negotiation_state'].get('round', 1),
        'stakeholders': session.get('characters', []),
        'playerProfile': session.get('player_profile', {}),
        'climateScore': session['negotiation_state'].get('negotiation_climate', 50),
        'messages': format_history_as_messages(
            session['negotiation_state'].get('history', []),
            current_user_id=(session.get('player_profile') or {}).get('id'),
            meta_history=session['negotiation_state'].get('history_meta', []) or [],
            characters=session.get('characters', []) or [],
        ),
        'issues': session['negotiation_state'].get('issues', {}),
        'constraintState': session['negotiation_state'].get('constraint_state', {}),
        'constraintLast': session['negotiation_state'].get('constraint_last', {}),
        'activeZoneId': active_zone_id,
        'activeIssueTag': active_issue_tag,
        'activeZoneFacts': (ZONE_FACT_ZONES.get(active_zone_id) or ZONE_FACT_ZONES.get('GLOBAL') or {}),
        'myPlayerId': (session.get('player_profile') or {}).get('id'),
        'outcome': session['negotiation_state'].get('outcome'),
        'winnerZone': session['negotiation_state'].get('winner_zone'),
        'winnerCounts': session['negotiation_state'].get('winner_counts'),
        'lastIntents': session['negotiation_state'].get('last_intents', {}) or {},
    })


@app.route('/api/rooms/<room_id>/select_role', methods=['POST'])
def select_role(room_id):
    room_id = (room_id or '').upper()
    room = ROOMS.get(room_id)
    if not room:
        return jsonify({'error': 'Room not found.'}), 404

    if room.get('phase') != 'lobby':
        return jsonify({'error': 'Room already started.'}), 400

    payload = request.get_json(silent=True) or {}
    player_id = payload.get('playerId') or session.get('player_id')
    if not player_id:
        return jsonify({'error': 'Not joined.'}), 403

    payload = request.get_json(silent=True) or {}
    role_id = payload.get('role')
    if not role_id:
        return jsonify({'error': 'Missing role.'}), 400

    if role_id not in ROLES and role_id not in ONBOARDING_DATA:
        return jsonify({'error': 'Invalid role.'}), 400

    # Allow 2 residents, 1 of everyone else
    existing_count = sum(1 for p in room.get('players', []) if p.get('role') == role_id)
    limit = 2 if role_id == 'resident_homeowner' else 1
    
    if existing_count >= limit:
        return jsonify({'error': 'Role already taken.'}), 400

    me = next((p for p in room['players'] if p.get('id') == player_id), None)
    if not me:
        return jsonify({'error': 'Player not found.'}), 404
    me['role'] = role_id

    # M2: Log Event
    log_event(room_id, player_id, 'ROLE_SELECTED', payload={'role': role_id}, role=role_id)

    socketio.emit('room_update', _get_public_room_state(room_id), room=room_id)
    return jsonify({'ok': True})


def _build_room_game_state(room):
    players = room.get('players', [])
    
    # Fixed 7 slots configuration
    ALL_SLOTS = [
        'council_planner', 
        'developer', 
        'community_activist', 
        'resident_homeowner', 
        'resident_homeowner',
        'urban_designer', 
        'potential_buyer'
    ]
    
    available_slots = list(ALL_SLOTS)

    characters = []
    for p in players:
        role_id = p.get('role')
        if not role_id:
            continue

        # Remove one instance of this role from available slots
        if role_id in available_slots:
            available_slots.remove(role_id)
            
        role_data = ROLES.get(role_id) or {}
        onboarding_role_data = ONBOARDING_DATA.get(role_id) or {}
        start_tokens = int(role_data.get('initial_influence_tokens', onboarding_role_data.get('tokens', 5)) or 5)
        max_tokens = _max_tokens_for_role(role_id, start_tokens)
        characters.append({
            'role_id': role_id,
            'role_name': role_data.get('name', onboarding_role_data.get('role_name', role_id)),
            'portrait': onboarding_role_data.get('portrait', None),
            'local_resident': False,
            'age': None,
            'has_children': False,
            'backstory': '',
            'influence_tokens': start_tokens,
            'starting_influence_tokens': start_tokens,
            'max_tokens': max_tokens,
            'stance_score': 50,
            'initial_stance': 'Support',
            'trust_value': 50,
            'influence': INFLUENCE_SCORES.get(role_id, 2),
            'id': p.get('id'),
            'is_player': True,
            'name': p.get('name', 'Player')
        })

    ai_count = int(room.get('config', {}).get('aiCount', 0))
    print(f"DEBUG: Room {room.get('id')} Config - AI Count: {ai_count}, Max Humans: {room.get('config', {}).get('maxHumans')}")
    
    ai_id_counter = 0
    used_names = set([str(c.get('name') or '').strip() for c in characters if c.get('name')])
    
    # Fill remaining slots with AI up to ai_count
    for i in range(ai_count):
        if not available_slots:
            break
            
        role_id = available_slots.pop(0)
        
        # Try to get data from ROLES (Scenario Data) first, then ONBOARDING_DATA
        role_data = ROLES.get(role_id) or {}
        onboarding_role_data = ONBOARDING_DATA.get(role_id) or {}
        
        start_tokens = int(role_data.get('initial_influence_tokens', 5))
        if not role_data and onboarding_role_data:
             start_tokens = int(onboarding_role_data.get('tokens', 5))

        max_tokens = _max_tokens_for_role(role_id, start_tokens)

        role_name = role_data.get('name', onboarding_role_data.get('role_name', role_id))
        description = role_data.get('description', onboarding_role_data.get('description', ''))
        trust = role_data.get('initial_trust', INITIAL_TRUST)

        ai_name = random.choice(SAMPLE_NAMES) if SAMPLE_NAMES else 'AI'
        if ai_name in used_names:
            tries = 0
            while tries < 20 and ai_name in used_names:
                ai_name = random.choice(SAMPLE_NAMES) if SAMPLE_NAMES else 'AI'
                tries += 1
            if ai_name in used_names:
                ai_name = f"{ai_name} {ai_id_counter + 1}"
        used_names.add(ai_name)

        ai_profile = {
            'id': f"ai_room_{ai_id_counter}",
            'role_id': role_id,
            'role_name': role_name,
            'name': ai_name,
            'is_player': False,
            'influence': INFLUENCE_SCORES.get(role_id, 1),
            'initial_stance': STANCES['neutral'],
            'stance_score': INITIAL_NEUTRAL_SCORE,
            'stance': get_stance_category(INITIAL_NEUTRAL_SCORE),
            'influence_tokens': start_tokens,
            'starting_influence_tokens': start_tokens,
            'max_tokens': max_tokens,
            'trust_value': trust,
            'polarization_score': 0,
            'previous_stance_category': get_stance_category(INITIAL_NEUTRAL_SCORE),
            'backstory': description
        }
        characters.append(ai_profile)
        ai_id_counter += 1

    host_id = room.get('hostId')
    def _sort_key(c):
        return (
            int(c.get('starting_influence_tokens', c.get('influence_tokens', 0)) or 0),
            str(c.get('role_id') or ''),
            str(c.get('id') or ''),
        )

    sorted_all = sorted([c for c in characters if c.get('id')], key=_sort_key)
    turn_order_base = [c.get('id') for c in sorted_all if c.get('id')]

    # Round 1: host always speaks first (forced), then others by ascending starting tokens (excluding host)
    if host_id and host_id in turn_order_base:
        sorted_others = [sid for sid in turn_order_base if sid != host_id]
        turn_order_round1 = [host_id] + sorted_others
    else:
        turn_order_round1 = turn_order_base

    # Effective turn order starts as round1 order; after round1 finishes, we switch to base order
    turn_order = list(turn_order_round1)

    default_c_state = ConstraintState()

    negotiation_state = {
        'round': 1,
        'history': [],
        'history_meta': [],
        'outcome': None,
        'negotiation_climate': 50,
        'active_zone_id': 'GLOBAL',
        'active_issue_tag': compute_issue_tag('GLOBAL'),
        'issues': {
            'affordable_share': 35,
            'cultural_venue_scale': 'medium',
            'housing_location_mix': 'balanced'
        },
        'constraint_state': state_to_dict(default_c_state),
        'constraint_last': {},
        'current_round_dialogue': {},
        'current_round_meta': {},
    }

    game_state = {
        'negotiation_state': negotiation_state,
        'characters': characters,
        'turn_order': turn_order,
        'turn_order_base': turn_order_base,
        'turn_order_round1': turn_order_round1,
        'turn_index': 0,
        'current_speaker': None,
        'turn_deadline_ts': None,
        'round_phase': 'preparation',
        'phase_deadline_ts': None,
        'phase_duration_sec': None,
        'submitted_by': [],
        'submitted_round': 1,
    }
    return game_state


def _regen_room_tokens(game_state):
    if not game_state:
        return
    negotiation_state = game_state.get('negotiation_state', {})
    # Only regen if we just started round 2 or later (round was just incremented)
    # The caller increments round BEFORE calling this.
    current_round = negotiation_state.get('round', 1)
    if current_round <= 1:
        return

    characters = game_state.get('characters', [])
    regen_amount = TOKEN_REGEN_RATE
    
    for char in characters:
        current = char.get('influence_tokens', 0)
        max_t = char.get('max_tokens', 12)
        char['influence_tokens'] = min(current + regen_amount, max_t)


def _finalize_multiplayer_game_if_needed(game_state):
    if not game_state:
        return
    negotiation_state = game_state.get('negotiation_state', {})
    current_round = negotiation_state.get('round', 1)
    
    if current_round > MAX_ROUNDS:
        # Game Over
        history = negotiation_state.get('history', [])
        issues = negotiation_state.get('issues', {})
        climate = negotiation_state.get('negotiation_climate', 50)
        characters = game_state.get('characters', [])
        
        # Use the existing check_victory function
        outcome_text = check_victory(characters, climate, issues, history)
        negotiation_state['outcome'] = outcome_text


def _is_ai_speaker(speaker_id, characters):
    if not speaker_id:
        return False
    char = next((c for c in characters if c.get('id') == speaker_id), None)
    return bool(char) and not char.get('is_player')


def _advance_turn_state(game_state):
    """Advance turn_index/current_speaker; if a round completes, commit dialogue to history and increment round.

    Special rule: After Round 1 completes, switch from turn_order_round1 to turn_order_base.
    """
    negotiation_state = game_state.get('negotiation_state', {})
    if negotiation_state.get('outcome'):
        return game_state
    turn_order = game_state.get('turn_order', []) or []
    turn_index = int(game_state.get('turn_index', 0) or 0)

    turn_index += 1

    # Round complete
    if turn_order and turn_index >= len(turn_order):
        current_round_dialogue = negotiation_state.get('current_round_dialogue', {}) or {}
        current_round_meta = negotiation_state.get('current_round_meta', {}) or {}
        negotiation_state.setdefault('history', []).append(current_round_dialogue)
        negotiation_state.setdefault('history_meta', []).append(current_round_meta)
        negotiation_state['current_round_dialogue'] = {}
        negotiation_state['current_round_meta'] = {}
        negotiation_state['round'] = int(negotiation_state.get('round', 1) or 1) + 1

        _regen_room_tokens(game_state)

        # After Round 1 ends, switch to base order (host is no longer forced first)
        if int(negotiation_state.get('round', 1)) == 2:
            base = game_state.get('turn_order_base')
            if base:
                game_state['turn_order'] = list(base)
                turn_order = game_state['turn_order']

        turn_index = 0

        _finalize_multiplayer_game_if_needed(game_state)

    game_state['turn_index'] = turn_index
    if (game_state.get('negotiation_state') or {}).get('outcome'):
        game_state['current_speaker'] = None
        game_state['turn_deadline_ts'] = None
    else:
        game_state['current_speaker'] = turn_order[turn_index] if turn_order and turn_index < len(turn_order) else None
        _set_turn_deadline(game_state)
    return game_state


def _set_turn_deadline(game_state):
    if not game_state:
        return
    if not game_state.get('current_speaker'):
        game_state['turn_deadline_ts'] = None
        return
    game_state['turn_deadline_ts'] = int(time.time() * 1000) + int(TURN_DURATION_SECONDS * 1000)


def _enforce_turn_timeout(room_id, room):
    if room and (room.get('game_state') or {}).get('phase_deadline_ts'):
        return False
    if not room or room.get('phase') != 'inGame':
        return False

    players = room.get('players', []) or []
    total_humans = sum(1 for p in players if p.get('id'))
    ready_by = room.get('readyBy') or []
    ready_count = 0
    try:
        ready_count = len(set([str(x) for x in ready_by if x]))
    except Exception:
        ready_count = 0
    if total_humans > 0 and ready_count < total_humans:
        return False

    game_state = room.get('game_state') or {}
    negotiation_state = game_state.get('negotiation_state') or {}
    if negotiation_state.get('outcome'):
        return False
    deadline = game_state.get('turn_deadline_ts')
    speaker = game_state.get('current_speaker')
    if not speaker or not deadline:
        if speaker and not deadline:
            _set_turn_deadline(game_state)
        return False

    try:
        now_ms = int(time.time() * 1000)
        deadline_ms = int(deadline)
    except Exception:
        _set_turn_deadline(game_state)
        return False

    if now_ms < deadline_ms:
        return False

    negotiation_state = game_state.get('negotiation_state') or {}
    log_event(room_id, speaker, 'TURN_TIMEOUT', round_idx=negotiation_state.get('round'), turn_idx=game_state.get('turn_index'))

    _advance_turn_state(game_state)
    _auto_play_ai_chain(room_id, room)

    try:
        socketio.emit('room_update', _get_public_room_state(room_id), room=room_id)
    except Exception:
        pass

    return True


def _auto_play_ai_chain(room_id, room, max_steps=None):
    """If current speaker is AI, auto-generate AI messages and advance until a human turn.

    Safety: stops after max_steps to prevent infinite loops.
    """
    game_state = room.get('game_state') or {}
    negotiation_state = game_state.get('negotiation_state') or {}
    characters = game_state.get('characters') or []

    if max_steps is None:
        max_steps = max(3, len(game_state.get('turn_order', []) or []))

    history = negotiation_state.get('history', []) or []
    issues = negotiation_state.get('issues', {}) or {}
    climate = negotiation_state.get('negotiation_climate', 50)
    zid = negotiation_state.get('active_zone_id') or 'GLOBAL'
    negotiation_state.setdefault('last_intents', {})

    # Seed context with latest message if available
    last_text = ''
    current_round_dialogue = negotiation_state.get('current_round_dialogue', {}) or {}
    if current_round_dialogue:
        # get last inserted speaker deterministically
        try:
            last_key = list(current_round_dialogue.keys())[-1]
            last_text = str(current_round_dialogue.get(last_key) or '')
        except Exception:
            last_text = ''
    elif history:
        last_round = history[-1] or {}
        try:
            last_key = list(last_round.keys())[-1]
            last_text = str(last_round.get(last_key) or '')
        except Exception:
            last_text = ''

    steps = 0
    while steps < max_steps:
        speaker_id = game_state.get('current_speaker')
        if not _is_ai_speaker(speaker_id, characters):
            break

        prompt_history = history
        if negotiation_state.get('current_round_dialogue'):
            prompt_history = history + [negotiation_state.get('current_round_dialogue')]

        responses = get_ai_responses(
            characters,
            prompt_history,
            last_text,
            climate,
            issues,
            only_ai_id=speaker_id,
            active_zone_id=zid,
            current_round=negotiation_state.get('round', 1),
        )
        ai_text = (responses.get(speaker_id) or {}).get('response')
        if not ai_text:
            ai_text = '...'

        ai_intent = infer_active_zone_id(ai_text, negotiation_state.get('last_intents', {}).get(speaker_id) or zid)
        if str(ai_intent).upper() not in ('A1', 'A2', 'K1'):
            ai_intent = (negotiation_state.get('last_intents', {}).get(speaker_id) or zid)
        ai_intent = str(ai_intent).upper() if ai_intent else str(zid).upper()
        if ai_intent not in ('A1', 'A2', 'K1'):
            ai_intent = 'A1'

        negotiation_state['last_intents'][speaker_id] = ai_intent
        negotiation_state['active_zone_id'] = ai_intent
        negotiation_state['active_issue_tag'] = compute_issue_tag(ai_intent)

        # Add AI message to current round dialogue
        negotiation_state.setdefault('current_round_dialogue', {})
        negotiation_state['current_round_dialogue'][speaker_id] = ai_text

        negotiation_state.setdefault('current_round_meta', {})
        ai_char = next((c for c in characters if c.get('id') == speaker_id), {})
        negotiation_state['current_round_meta'][speaker_id] = {
            'zone_id': ai_intent,
            'issue_tag': compute_issue_tag(ai_intent),
            'role_id': (ai_char or {}).get('role_id'),
            'intent': ai_intent,
        }

        last_text = ai_text

        # Advance
        _advance_turn_state(game_state)
        steps += 1

    room['game_state'] = game_state
    return


@app.route('/api/export/room/<room_id>/session')
def export_room_session(room_id):
    """Export full game session data for analysis."""
    room_id = (room_id or '').upper()
    conn, db_type = get_db_connection()
    try:
        query = "SELECT * FROM events WHERE room_id = %s ORDER BY ts ASC" if db_type == 'postgres' else "SELECT * FROM events WHERE room_id = ? ORDER BY ts ASC"
        
        if db_type == 'postgres':
            with conn.cursor() as cur:
                cur.execute(query, (room_id,))
                rows = cur.fetchall()
        else:
            rows = conn.execute(query, (room_id,)).fetchall()
            
        events = [dict(row) for row in rows]
        
        # Structure for v0 Analysis
        export_data = {
            'meta': {
                'room_id': room_id,
                'exported_at': time.time(),
                'version': 'v0'
            },
            'timeline': events
        }
        
        return jsonify(export_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/rooms/<room_id>/start', methods=['POST'])
def start_room(room_id):
    room_id = (room_id or '').upper()
    room = ROOMS.get(room_id)
    if not room:
        return jsonify({'error': 'Room not found.'}), 404

    payload = request.get_json(silent=True) or {}
    player_id = payload.get('playerId') or session.get('player_id')
    if not player_id:
        return jsonify({'error': 'Not joined.'}), 403

    if room.get('hostId') != player_id:
        return jsonify({'error': 'Only host can start.'}), 403

    if room.get('phase') != 'lobby':
        return jsonify({'ok': True})

    if not all(p.get('role') for p in room.get('players', [])):
        return jsonify({'error': 'All players must select a role.'}), 400

    room['game_state'] = _build_room_game_state(room)
    room['phase'] = 'inGame'
    room['readyBy'] = []
    try:
        if room.get('game_state') and room['game_state'].get('turn_deadline_ts'):
            room['game_state']['turn_deadline_ts'] = None
    except Exception:
        pass
    try:
        if room.get('game_state') and room['game_state'].get('phase_deadline_ts'):
            room['game_state']['phase_deadline_ts'] = None
    except Exception:
        pass

    # M2: Log Event
    log_event(room_id, player_id, 'GAME_STARTED', payload={'config': room.get('config')})

    socketio.emit('room_update', _get_public_room_state(room_id), room=room_id)
    socketio.emit('game_start', {'url': url_for('game')}, room=room_id)
    return jsonify({'ok': True})


@app.route('/api/rooms/<room_id>/ready', methods=['POST'])
def mark_player_ready(room_id):
    room_id = (room_id or '').upper()
    room = ROOMS.get(room_id)
    if not room:
        return jsonify({'error': 'Room not found.'}), 404

    if room.get('phase') != 'inGame':
        return jsonify({'error': 'Game not started.'}), 400

    payload = request.get_json(silent=True) or {}
    player_id = payload.get('playerId') or session.get('player_id')
    if not player_id:
        return jsonify({'error': 'Not joined.'}), 403

    players = room.get('players', []) or []
    if not any(p.get('id') == player_id for p in players):
        return jsonify({'error': 'Player not found.'}), 404

    ready_by = room.get('readyBy') or []
    if player_id not in ready_by:
        ready_by.append(player_id)
    room['readyBy'] = ready_by

    total_humans = sum(1 for p in players if p.get('id'))
    ready_count = 0
    try:
        ready_count = len(set([str(x) for x in ready_by if x]))
    except Exception:
        ready_count = 0

    # Start phase timer when all humans are ready
    game_state = room.get('game_state') or {}
    if total_humans > 0 and ready_count >= total_humans and game_state:
        if not game_state.get('phase_deadline_ts'):
            rp = str(game_state.get('round_phase') or 'preparation').lower()
            negotiation_state = game_state.get('negotiation_state') or {}
            try:
                round_idx = int(negotiation_state.get('round', 1) or 1)
            except Exception:
                round_idx = 1
            if rp == 'submission':
                _auto_fill_ai_submissions(game_state)
            _set_phase_deadline(game_state, _round_phase_duration_seconds(rp, round_idx))
            room['game_state'] = game_state

    try:
        socketio.emit('room_update', _get_public_room_state(room_id), room=room_id)
    except Exception:
        pass

    return jsonify({
        'ok': True,
        'phase': 'inGame' if (total_humans > 0 and ready_count >= total_humans) else 'ready',
        'readyCount': ready_count,
        'readyTotal': total_humans,
        'iAmReady': True,
    })


@socketio.on('join_room_socket')
def join_room_socket(data):
    room_id = ((data or {}).get('roomId') or '').upper()
    if not room_id:
        return
    join_room(room_id)
    if room_id in ROOMS:
        socketio.emit('room_update', _get_public_room_state(room_id), room=room_id)


# --- 3D Synchronization ---
@socketio.on('update_scene_object')
def handle_scene_update(data):
    """
    Handle real-time 3D object updates.
    data = {
        'roomId': '...',
        'objectId': '...',
        'transform': { 'position': {...}, 'rotation': {...}, 'scale': {...} },
        'playerId': '...'
    }
    """
    room_id = (data.get('roomId') or '').upper()
    if room_id not in ROOMS:
        return

    obj_id = data.get('objectId')
    transform = data.get('transform')
    
    if not obj_id or not transform:
        return

    room = ROOMS[room_id]
    
    # Init scene_state if missing (for existing rooms)
    if 'scene_state' not in room:
        room['scene_state'] = {'objects': {}, 'last_modified': time.time()}

    # Update state
    room['scene_state']['objects'][obj_id] = transform
    room['scene_state']['last_modified'] = time.time()

    # Broadcast to others in the room
    # include_self=False is implicit if we use 'broadcast=True' but 'room=...' targets everyone usually
    # We want to echo back or let client handle optimism. 
    # Usually client updates locally first, so we might want to skip sender if possible.
    # For now, broadcast to all, client can filter by 'playerId' if needed.
    # User Request: Disable Sync (Independent Views)
    # socketio.emit('scene_object_updated', data, room=room_id)

    # M4: Log 3D Event for Persistence
    try:
        player_id = data.get('playerId')
        game_state = room.get('game_state') or {}
        negotiation_state = game_state.get('negotiation_state') or {}
        
        log_event(
            room_id, 
            player_id, 
            'SCENE_UPDATE', 
            payload={'objectId': obj_id, 'transform': transform},
            round_idx=negotiation_state.get('round'),
            turn_idx=game_state.get('turn_index')
        )
    except Exception as e:
        print(f"Error logging scene update: {e}")


@app.route('/api/rooms/<room_id>/send', methods=['POST'])
def send_message(room_id):
    """Submit a speech + vote during the submission phase."""
    room_id = (room_id or '').upper()
    room = ROOMS.get(room_id)
    if not room:
        return jsonify({'error': 'Room not found.'}), 404

    if room.get('phase') != 'inGame':
        return jsonify({'error': 'Game not started.'}), 400

    players = room.get('players', []) or []
    total_humans = sum(1 for p in players if p.get('id'))
    ready_by = room.get('readyBy') or []
    ready_count = 0
    try:
        ready_count = len(set([str(x) for x in ready_by if x]))
    except Exception:
        ready_count = 0
    if total_humans > 0 and ready_count < total_humans:
        return jsonify({'error': 'Waiting for all players to start.', 'phase': 'ready', 'readyCount': ready_count, 'readyTotal': total_humans}), 400

    _enforce_phase_timeout(room_id, room)

    payload = request.get_json(silent=True) or {}
    player_id = payload.get('playerId') or session.get('player_id')
    text = payload.get('text', '').strip()
    intent = payload.get('intent') # v0: Explicit Intent Marker
    influence_action = payload.get('influenceAction')

    if not player_id:
        return jsonify({'error': 'Player ID required.'}), 400

    game_state = room.get('game_state', {})
    if str(game_state.get('round_phase') or '').lower() != 'submission':
        return jsonify({'error': 'Not in submission phase.', 'roundPhase': game_state.get('round_phase')}), 400

    if not text:
        return jsonify({'error': 'Message text required.'}), 400

    submitted_by = list(game_state.get('submitted_by') or [])
    if player_id in submitted_by:
        return jsonify({'error': 'Already submitted.', 'hasSubmitted': True}), 400

    characters = game_state.get('characters') or []
    me = next((c for c in characters if c.get('id') == player_id), {})
    role_id = (me or {}).get('role_id')
    if not role_id:
        return jsonify({'error': 'Player role not found.'}), 400

    if not influence_action:
        influence_action = 'gentle_persuasion'

    negotiation_state = game_state.get('negotiation_state', {})
    if negotiation_state.get('outcome'):
        return jsonify({'error': 'Game over.', 'outcome': negotiation_state.get('outcome')}), 400
    negotiation_state.setdefault('player_action_history', {})
    action_history = negotiation_state['player_action_history'].get(player_id, [])
    cost = _compute_influence_cost(influence_action, role_id, action_history=action_history)
    if cost is None:
        return jsonify({'error': 'Invalid influence action.'}), 400

    current_tokens = int((me or {}).get('influence_tokens', 0) or 0)
    if current_tokens < cost:
        return jsonify({'error': 'Not enough tokens.', 'cost': cost, 'tokens': current_tokens}), 400

    me['influence_tokens'] = current_tokens - cost
    max_tokens = _max_tokens_for_role(role_id, me.get('starting_influence_tokens', current_tokens))
    me['max_tokens'] = max_tokens

    action_history = list(action_history)
    action_history.append(influence_action)
    if len(action_history) > 5:
        action_history = action_history[-5:]
    negotiation_state['player_action_history'][player_id] = action_history

    game_state['characters'] = characters

    negotiation_state.setdefault('current_round_dialogue', {})
    negotiation_state['current_round_dialogue'][player_id] = text

    # v0: Use explicit intent if provided, otherwise infer
    if intent:
        zid = intent
    else:
        zid = infer_active_zone_id(text, negotiation_state.get('active_zone_id') or 'GLOBAL')
    
    zid_norm = str(zid).upper() if zid else 'GLOBAL'
    negotiation_state['active_zone_id'] = zid_norm
    negotiation_state['active_issue_tag'] = compute_issue_tag(zid_norm)
    negotiation_state.setdefault('last_intents', {})
    if zid_norm in ('A1', 'A2', 'K1'):
        negotiation_state['last_intents'][player_id] = zid_norm
    negotiation_state.setdefault('current_round_meta', {})
    negotiation_state['current_round_meta'][player_id] = {
        'zone_id': zid_norm,
        'issue_tag': negotiation_state.get('active_issue_tag'),
        'role_id': role_id,
        'intent': (str(intent).upper() if intent else zid_norm),
        'influence_action': influence_action,
        'influence_cost': cost
    }

    # M2: Log Event
    log_event(
        room_id, 
        player_id, 
        'MESSAGE_SENT', 
        payload={'text': text, 'zone': zid, 'intent': intent, 'influenceAction': influence_action, 'cost': cost, 'tokensAfter': me.get('influence_tokens')}, 
        role=role_id, 
        round_idx=negotiation_state.get('round'), 
        turn_idx=game_state.get('turn_index')
    )

    submitted_by.append(player_id)
    game_state['submitted_by'] = submitted_by
    try:
        game_state['submitted_round'] = int(negotiation_state.get('round', 1) or 1)
    except Exception:
        game_state['submitted_round'] = negotiation_state.get('round', 1)

    characters = game_state.get('characters') or []
    human_ids = [c.get('id') for c in characters if c.get('is_player') and c.get('id')]
    all_humans_submitted = all((hid in submitted_by) for hid in human_ids) if human_ids else True

    if all_humans_submitted:
        _advance_round_phase(room_id, room)

    # Broadcast update to all clients in the room
    socketio.emit('room_update', _get_public_room_state(room_id), room=room_id)

    return jsonify({
        'ok': True,
        'roundPhase': (room.get('game_state') or {}).get('round_phase'),
        'roundIndex': negotiation_state.get('round', 1),
        'outcome': negotiation_state.get('outcome'),
        'winnerZone': negotiation_state.get('winner_zone'),
        'winnerCounts': negotiation_state.get('winner_counts'),
    })


@app.route('/api/rooms/<room_id>/advance_turn', methods=['POST'])
def advance_turn(room_id):
    """Host-only: force advance the phase engine (debug)."""
    room_id = (room_id or '').upper()
    room = ROOMS.get(room_id)
    if not room:
        return jsonify({'error': 'Room not found.'}), 404

    if room.get('phase') != 'inGame':
        return jsonify({'error': 'Game not started.'}), 400

    players = room.get('players', []) or []
    total_humans = sum(1 for p in players if p.get('id'))
    ready_by = room.get('readyBy') or []
    ready_count = 0
    try:
        ready_count = len(set([str(x) for x in ready_by if x]))
    except Exception:
        ready_count = 0
    if total_humans > 0 and ready_count < total_humans:
        return jsonify({'error': 'Waiting for all players to start.', 'phase': 'ready', 'readyCount': ready_count, 'readyTotal': total_humans}), 400

    payload = request.get_json(silent=True) or {}
    player_id = payload.get('playerId') or session.get('player_id')
    if not player_id:
        return jsonify({'error': 'Not joined.'}), 403

    if room.get('hostId') != player_id:
        return jsonify({'error': 'Only host can advance turn.'}), 403

    game_state = room.get('game_state', {})
    negotiation_state = game_state.get('negotiation_state', {})

    if negotiation_state.get('outcome'):
        return jsonify({'error': 'Game over.', 'outcome': negotiation_state.get('outcome')}), 400

    # M2: Log Event
    log_event(room_id, player_id, 'PHASE_FORCED_ADVANCE', round_idx=negotiation_state.get('round'), turn_idx=game_state.get('turn_index'))

    _advance_round_phase(room_id, room)

    socketio.emit('room_update', _get_public_room_state(room_id), room=room_id)
    return jsonify({
        'ok': True,
        'roundPhase': (room.get('game_state') or {}).get('round_phase'),
        'roundIndex': negotiation_state.get('round', 1),
        'outcome': negotiation_state.get('outcome'),
        'winnerZone': negotiation_state.get('winner_zone'),
        'winnerCounts': negotiation_state.get('winner_counts'),
    })


@app.route('/api/rooms/<room_id>/timeout_turn', methods=['POST'])
def timeout_turn(room_id):
    """Client-triggered timeout check: advance phase if deadline has passed."""
    room_id = (room_id or '').upper()
    room = ROOMS.get(room_id)
    if not room:
        return jsonify({'error': 'Room not found.'}), 404

    if room.get('phase') != 'inGame':
        return jsonify({'error': 'Game not started.'}), 400

    players = room.get('players', []) or []
    total_humans = sum(1 for p in players if p.get('id'))
    ready_by = room.get('readyBy') or []
    ready_count = 0
    try:
        ready_count = len(set([str(x) for x in ready_by if x]))
    except Exception:
        ready_count = 0
    if total_humans > 0 and ready_count < total_humans:
        return jsonify({'timedOut': False, 'phase': 'ready', 'readyCount': ready_count, 'readyTotal': total_humans}), 200

    changed = _enforce_phase_timeout(room_id, room)
    game_state = room.get('game_state') or {}
    negotiation_state = game_state.get('negotiation_state') or {}
    dl = game_state.get('phase_deadline_ts')
    try:
        remaining = max(0, int(math.ceil(((int(dl or 0)) - int(time.time() * 1000)) / 1000.0))) if dl else None
    except Exception:
        remaining = None

    return jsonify({
        'ok': True,
        'timedOut': bool(changed),
        'roundPhase': game_state.get('round_phase'),
        'phaseDeadlineTs': dl,
        'phaseRemainingSec': remaining,
        'phaseDurationSec': game_state.get('phase_duration_sec'),
        'outcome': negotiation_state.get('outcome'),
        'winnerZone': negotiation_state.get('winner_zone'),
        'winnerCounts': negotiation_state.get('winner_counts'),
    })


@app.route('/api/rooms/<room_id>/end_round', methods=['POST'])
def end_round(room_id):
    """Host-only: force end current round and move to next round."""
    room_id = (room_id or '').upper()
    room = ROOMS.get(room_id)
    if not room:
        return jsonify({'error': 'Room not found.'}), 404

    if room.get('phase') != 'inGame':
        return jsonify({'error': 'Game not started.'}), 400

    players = room.get('players', []) or []
    total_humans = sum(1 for p in players if p.get('id'))
    ready_by = room.get('readyBy') or []
    ready_count = 0
    try:
        ready_count = len(set([str(x) for x in ready_by if x]))
    except Exception:
        ready_count = 0
    if total_humans > 0 and ready_count < total_humans:
        return jsonify({'error': 'Waiting for all players to start.', 'phase': 'ready', 'readyCount': ready_count, 'readyTotal': total_humans}), 400

    payload = request.get_json(silent=True) or {}
    player_id = payload.get('playerId') or session.get('player_id')
    if not player_id:
        return jsonify({'error': 'Not joined.'}), 403

    if room.get('hostId') != player_id:
        return jsonify({'error': 'Only host can end round.'}), 403

    game_state = room.get('game_state', {})
    negotiation_state = game_state.get('negotiation_state', {})

    # M2: Log Event
    log_event(room_id, player_id, 'ROUND_FORCED_END', round_idx=negotiation_state.get('round'))

    if negotiation_state.get('outcome'):
        return jsonify({'ok': True, 'outcome': negotiation_state.get('outcome')})

    _ensure_submission_defaults(game_state)
    _finalize_submission_round(game_state)
    negotiation_state = (game_state.get('negotiation_state') or {})

    if negotiation_state.get('outcome'):
        game_state['round_phase'] = 'game_over'
        game_state['phase_deadline_ts'] = None
        game_state['phase_duration_sec'] = None
    else:
        game_state['round_phase'] = 'transition'
        game_state['submitted_by'] = []
        try:
            game_state['submitted_round'] = int(negotiation_state.get('round', 1) or 1)
        except Exception:
            game_state['submitted_round'] = negotiation_state.get('round', 1)
        _set_phase_deadline(game_state, _round_phase_duration_seconds('transition', negotiation_state.get('round', 1)))

    room['game_state'] = game_state
    socketio.emit('room_update', _get_public_room_state(room_id), room=room_id)
    return jsonify({
        'ok': True,
        'roundPhase': game_state.get('round_phase'),
        'roundIndex': negotiation_state.get('round', 1),
        'outcome': negotiation_state.get('outcome'),
        'winnerZone': negotiation_state.get('winner_zone'),
        'winnerCounts': negotiation_state.get('winner_counts'),
    })


# --- M2: Data Export APIs ---

@app.route('/api/export/rooms')
def export_rooms():
    """List all rooms with basic stats."""
    if not session.get('authenticated'):
         return jsonify({'error': 'Unauthorized'}), 401

    conn, db_type = get_db_connection()
    try:
        # Get room stats from DB
        if db_type == 'postgres':
             with conn.cursor() as cur:
                cur.execute("""
                    SELECT room_id, count(*) as event_count, min(ts) as start_time, max(ts) as last_update 
                    FROM events GROUP BY room_id
                """)
                rows = cur.fetchall()
        else:
             rows = conn.execute("""
                SELECT room_id, count(*) as event_count, min(ts) as start_time, max(ts) as last_update 
                FROM events GROUP BY room_id
            """).fetchall()
        
        stats = {row['room_id']: dict(row) for row in rows}
    except Exception as e:
        print(f"Export Error: {e}")
        stats = {}
    finally:
        conn.close()

    # Merge with active memory rooms
    data = []
    all_ids = set(ROOMS.keys()) | set(stats.keys())
    
    for rid in all_ids:
        mem_room = ROOMS.get(rid, {})
        db_stat = stats.get(rid, {})
        
        data.append({
            'roomId': rid,
            'active': rid in ROOMS,
            'phase': mem_room.get('phase', 'archived'),
            'playerCount': len(mem_room.get('players', [])) if rid in ROOMS else 0,
            'eventCount': db_stat.get('event_count', 0),
            'startTime': db_stat.get('start_time'),
            'lastUpdate': db_stat.get('last_update')
        })
    
    return jsonify(data)

@app.route('/api/export/room/<room_id>/events')
def export_room_events(room_id):
    """Export raw events for a room."""
    if not session.get('authenticated'):
         return jsonify({'error': 'Unauthorized'}), 401
         
    conn, db_type = get_db_connection()
    try:
        query = "SELECT * FROM events WHERE room_id = %s ORDER BY ts ASC" if db_type == 'postgres' else "SELECT * FROM events WHERE room_id = ? ORDER BY ts ASC"
        params = (room_id,)
        
        if db_type == 'postgres':
             with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
                events = [dict(row) for row in rows]
        else:
             rows = conn.execute(query, params).fetchall()
             events = [dict(row) for row in rows]
             
        # Parse payload_json
        for e in events:
            if e.get('payload_json'):
                try:
                    e['payload'] = json.loads(e['payload_json'])
                except:
                    e['payload'] = {}
            del e['payload_json']
            
        return jsonify(events)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/export/room/<room_id>/transcript')
def export_room_transcript(room_id):
    """Export readable transcript."""
    if not session.get('authenticated'):
         return jsonify({'error': 'Unauthorized'}), 401

    conn, db_type = get_db_connection()
    try:
        query = "SELECT * FROM events WHERE room_id = %s AND event_type = 'MESSAGE_SENT' ORDER BY ts ASC" if db_type == 'postgres' else "SELECT * FROM events WHERE room_id = ? AND event_type = 'MESSAGE_SENT' ORDER BY ts ASC"
        params = (room_id,)
        
        if db_type == 'postgres':
             with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
        else:
             rows = conn.execute(query, params).fetchall()
        
        transcript = []
        for row in rows:
            payload = {}
            try:
                payload = json.loads(row['payload_json'])
            except:
                pass
            
            transcript.append({
                'ts': row['ts'],
                'role': row['role'],
                'text': payload.get('text', ''),
                'zone': payload.get('zone', ''),
                'round': row['round_index']
            })
            
        return jsonify(transcript)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()


if __name__ == '__main__':
    # Make sure to create a .env file with your OPENAI_API_KEY
    # Example: OPENAI_API_KEY='sk-...'    
    ssl_cert = os.environ.get('SSL_CERT_FILE')
    ssl_key = os.environ.get('SSL_KEY_FILE')
    run_kwargs = {
        'debug': True,
        'host': '0.0.0.0',
        'port': 5006,
        'allow_unsafe_werkzeug': True,
    }
    if ssl_cert and ssl_key:
        run_kwargs['ssl_context'] = (ssl_cert, ssl_key)
    socketio.run(app, **run_kwargs)

