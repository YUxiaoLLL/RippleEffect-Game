import json
import os
import re


def load_zone_facts(base_dir):
    try:
        with open(os.path.join(base_dir, 'zone_facts.json'), 'r') as f:
            data = json.load(f)
            zones = data.get('zones') or {}
            return data, zones
    except Exception:
        return {}, {}


def infer_active_zone_id(text, fallback='GLOBAL'):
    if not text:
        return fallback
    m = re.search(r'\b(A1|A2|K1)\b', str(text), flags=re.IGNORECASE)
    if not m:
        return fallback
    return m.group(1).upper()


def compute_issue_tag(zone_id):
    zid = (zone_id or '').upper()
    if zid == 'K1':
        return 'affordability'
    if zid in ('A1', 'A2'):
        return 'workspace'
    return 'system'


def zone_context_text(zone_id, zone_zones):
    zid = (zone_id or 'GLOBAL').upper()
    zone = (zone_zones.get(zid) or zone_zones.get('GLOBAL') or {})
    hard_facts = zone.get('hard_facts') or {}
    goals = zone.get('goals') or []
    issues = zone.get('typical_issues') or []
    must_keywords = zone.get('must_mention_keywords') or []

    facts_lines = []
    for k, v in hard_facts.items():
        facts_lines.append(f"- {k}: {v}")

    goals_lines = [f"- {g}" for g in goals]
    issues_lines = [f"- {i}" for i in issues]

    txt = (
        "[Zone Context]\n"
        f"- zone_id: {zid}\n"
        + (f"- label: {zone.get('label')}\n" if zone.get('label') else '')
        + ("- hard_facts:\n" + "\n".join(facts_lines) + "\n" if facts_lines else '')
        + ("- goals:\n" + "\n".join(goals_lines) + "\n" if goals_lines else '')
        + ("- typical_issues:\n" + "\n".join(issues_lines) + "\n" if issues_lines else '')
        + ("- must_mention_keywords: " + ", ".join(must_keywords) + "\n" if must_keywords else '')
    )

    return zone, txt


def validate_ai_dialogue(
    dialogue,
    role_id,
    zone_id,
    zone_info,
    role_voice_keywords,
    require_zone_id=False,
    require_zone_fact=False,
    require_role_voice=False,
    require_question_by_role=False,
    allow_other_zones=False,
):
    text = (dialogue or '').strip()
    if not text:
        return ['empty']

    errors = []
    zid = (zone_id or 'GLOBAL').upper()

    if require_zone_id and zid != 'GLOBAL' and not re.search(rf'\b{re.escape(zid)}\b', text):
        errors.append('missing_zone_id')

    must_keywords = (zone_info or {}).get('must_mention_keywords') or []
    if require_zone_fact and must_keywords:
        hit = False
        for kw in must_keywords:
            if kw and kw in text:
                hit = True
                break
        if not hit:
            errors.append('missing_zone_fact')

    if not allow_other_zones:
        for other in ('A1', 'A2', 'K1'):
            if other == zid:
                continue
            if re.search(rf'\b{other}\b', text):
                errors.append('mentions_other_zone')
                break

    role = (role_id or '').strip()
    kws = role_voice_keywords.get(role) or []
    if require_role_voice and kws:
        low = text.lower()
        if not any(k in low for k in kws):
            errors.append('role_voice_missing')

    if require_question_by_role and role == 'community_activist':
        if '?' not in text:
            errors.append('activist_needs_question')

    return errors
