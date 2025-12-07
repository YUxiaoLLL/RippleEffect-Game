# Negotiation MVP Integration Guide

## Overview
This MVP combines your existing Three.js 3D visualization with a mobile-first negotiation interface, fully integrated with your Flask backend.

## What Was Built

### 1. **New MVP Template** (`templates/negotiation_mvp.html`)
A mobile-optimized, iPhone-ready negotiation interface with:

#### **Key Features:**
- **Header Bar**: Back button, round title, stakeholder overview
- **3D Preview Panel (45% height)**: 
  - Embeds your existing Three.js visualization via iframe
  - Touch-enabled rotate and zoom
  - Expandable to fullscreen
  - Shows real-time impact labels
- **Impact Bar**: Displays top 3 affected stakeholders with emoji and scores
- **Chat Area**: 
  - Scrollable message history
  - Clickable AI avatars that open stakeholder profiles
  - Auto-scrolls to latest message
- **Quick Action Buttons**: Preset persuasion options in 2-column layout
- **Input Bar**: iOS-style rounded input with send button
- **Stakeholder Profile Popup**: 
  - Triggered by tapping any AI avatar
  - Shows all attributes (role, age, stance, tokens, etc.)
  - Bottom sheet style with swipe-down dismiss

### 2. **New Flask Routes** (in `app.py`)

#### `/negotiation_mvp` (GET, POST)
- Main MVP interface endpoint
- Reuses existing negotiation logic for POST requests
- Renders the new template with session data

#### `/api/negotiation/state` (GET)
- JSON API endpoint for AJAX state updates
- Returns current round, stakeholders, messages, climate score
- Enables dynamic updates without page reload

### 3. **Helper Functions**
- `format_history_as_messages()`: Converts dialogue history to frontend message format

## How It Works

### Data Flow:
1. User customizes character → redirected to `/negotiation_mvp`
2. Template receives Jinja2 data: `characters`, `player_profile`, `state`, `climate_score`
3. JavaScript initializes with this data and renders UI
4. User sends message → POST to `/negotiation_mvp`
5. Flask processes with existing logic → redirects back
6. Template re-renders with updated state

### 3D Visualization Integration:
- Three.js scene is embedded via iframe: `/new_3d_pipeline/frontend_threejs/index.html`
- Can be expanded to fullscreen
- Touch gestures work (pinch-zoom, rotate)
- Communicates with backend for real-time updates

## Usage

### Starting the Server:
```bash
cd /Users/peterliang/Documents/RippleEffect
python app.py
```

### Accessing the MVP:
1. Open: `http://127.0.0.1:5000/`
2. Select a role
3. Customize your character
4. **Automatically redirected to MVP interface**

### Features to Test:
- ✅ **3D Model Preview**: Should show your Three.js scene
- ✅ **Tap avatar**: Opens stakeholder profile popup
- ✅ **Quick actions**: Pre-fills message input
- ✅ **Send message**: Triggers AI responses
- ✅ **Impact bar**: Updates based on stakeholder reactions
- ✅ **Expand 3D**: Fullscreen visualization

## Alignment with Your Specs

### ✅ Implemented from LEVEL 3 Requirements:
1. **Header Bar** - Back, round title, stakeholder overview icon
2. **3D Preview Panel** - 45% height, expand icon, impact labels
3. **Proposal Impact Bar** - Top 3 stakeholders with emoji + scores
4. **Chat Area** - Scrollable, left-aligned AI, right-aligned player
5. **Quick Action Buttons** - 2-column wrap, iOS-style cards
6. **Input Bar** - Rounded white input, send button, safe area
7. **Stakeholder Profile Popup** - Triggered by avatar tap, shows all attributes

### 📋 Still TODO (for full production):
- **LEVEL 4**: Outcome Evaluation page (create similar template)
- **Enhanced animations**: Smoother transitions, particle effects
- **Offline support**: Service worker, local storage
- **Push notifications**: Round reminders
- **Advanced AI**: More nuanced responses based on personality traits

## Customization

### Adjusting 3D Panel Height:
In `negotiation_mvp.html`, line 111:
```html
<div id="3dPanel" class="... h-[45vh] ...">
```
Change `45vh` to desired percentage (e.g., `50vh`, `40vh`)

### Changing Colors:
Tailwind config is at top of file. Edit:
```javascript
colors: {
    'success': '#16A34A',   // Green for positive
    'warning': '#CA8A04',   // Yellow for neutral
    'destructive': '#DC2626' // Red for negative
}
```

### Adding More Quick Actions:
In `negotiation_mvp.html`, line 143-157:
```html
<button class="action-btn ..." data-action="new_action">
    New Action Text
</button>
```
Then add to `actionTexts` object in JavaScript (line 406+)

## Mobile Optimization

### Safe Areas (iPhone notch/home bar):
```css
.safe-area-bottom {
    padding-bottom: env(safe-area-inset-bottom);
}
```

### Touch Gestures:
- Single finger: Rotate 3D
- Pinch: Zoom 3D
- Swipe down on popup: Dismiss
- Tap outside: Close modals

## Troubleshooting

### "No active negotiation" error:
- Ensure you go through role selection → customization first
- Check Flask session is enabled (it is in `app.py`)

### 3D not showing:
- Verify `/new_3d_pipeline/frontend_threejs/index.html` exists
- Check Flask is serving static files correctly
- Try accessing iframe URL directly

### Avatars not clickable:
- Check `showStakeholderProfile()` function (line 320+)
- Verify `stakeholder.id` matches between backend and frontend

## Next Steps

### For Full Production:
1. **Create Outcome Evaluation page** (LEVEL 4 specs)
2. **Add WebSocket support** for real-time updates (no page reload)
3. **Implement progressive web app** (installable on phone)
4. **Add haptic feedback** for iOS
5. **Optimize 3D performance** for mobile GPUs
6. **Add analytics** to track user engagement

### Integration with Existing Features:
- Your scenario system (`scenario_canadawater.json`) ✅ Already integrated
- AI responses via OpenAI ✅ Working
- Token system ✅ Implemented
- Trust/stance mechanics ✅ Active
- Issue tracking ✅ Connected to 3D

## Architecture

```
Frontend (Mobile-First HTML/CSS/JS)
    ↓
Flask Backend (app.py)
    ↓
Session Management (flask-session)
    ↓
OpenAI API (GPT-4)
    ↓
Scenario Data (JSON)
```

## Files Modified/Created

### Created:
- `/templates/negotiation_mvp.html` (580 lines)
- This README

### Modified:
- `/app.py`:
  - Added `/negotiation_mvp` route (line 526-552)
  - Added `/api/negotiation/state` endpoint (line 554-567)
  - Added `format_history_as_messages()` helper (line 569-582)
  - Updated customization redirect (line 371-374)

## Questions?

If you encounter any issues or want to extend functionality, key places to look:

1. **UI Layout**: `templates/negotiation_mvp.html` (HTML structure)
2. **Styling**: Inline Tailwind classes + `<style>` section
3. **State Management**: JavaScript object at line 230+
4. **Backend Logic**: `/app.py` routes and helpers
5. **Data Flow**: Jinja2 templates at line 237-242

---

**Built with:** Flask, Jinja2, Tailwind CSS, Three.js
**Optimized for:** iPhone 14/15/16 Pro (430×932px)
**Browser Support:** Modern browsers with ES6+ support
