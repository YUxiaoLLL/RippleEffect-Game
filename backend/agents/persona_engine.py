import random
from .persona_data import ORIGINS, LIFE_STAGES, PAIN_POINTS, STYLES, QUIRKS, ROLE_MAPPING

def generate_dna_persona(role_id, role_name):
    """
    Synthesizes a unique character based on DNA factors tailored to the role.
    """
    # 1. 获取该角色的约束条件 (Constraints)
    mapping = ROLE_MAPPING.get(role_id, {
        "allowed_origins": ["local_deep", "local_recent", "outsider"],
        "allowed_pains": ["precariat", "middle_class", "wealthy", "corporate"],
        "allowed_styles": ["street", "corporate", "academic", "nimby", "activist"],
        "default_flexibility": [3, 8]
    })

    # 2. 抽取 DNA 因子 (Rolling the dice)
    
    # Factor A: Origin
    origin_key = random.choice(mapping["allowed_origins"])
    origin_story = random.choice(ORIGINS[origin_key])

    # Factor B: Life Stage
    life_stage = random.choice(LIFE_STAGES)

    # Factor C: Pain Point (Deep Motivation)
    pain_key = random.choice(mapping["allowed_pains"])
    pain_point = random.choice(PAIN_POINTS[pain_key])

    # Factor D: Style (Voice)
    style_key = random.choice(mapping["allowed_styles"])
    style_data = STYLES[style_key]

    # Factor E: Quirk
    quirk = random.choice(QUIRKS)

    # Factor F: Flexibility (Hidden Stat)
    flex_range = mapping["default_flexibility"]
    flexibility = random.randint(flex_range[0], flex_range[1])

    # 3. 合成叙事文本 (Synthesize Narrative)
    # 这是一个“有理有据”的自我介绍，将所有因子串联起来
    narrative_bio = (
        f"I am a {life_stage['stage']} who {origin_story.lower()} "
        f"My primary focus right now is {life_stage['focus'].lower()}. "
        f"My biggest worry regarding this project is {pain_point.lower()} "
    )

    # 4. 构建 System Prompt 指令块
    system_prompt_addendum = (
        f"--- CHARACTER DNA PROFILE ---\n"
        f"**Bio & Motivation:** {narrative_bio}\n"
        f"**Personality Quirk:** {quirk}\n"
        f"**Speaking Style:** '{style_key.upper()}'\n"
        f"  - Description: {style_data['desc']}\n"
        f"  - Keywords to use: {', '.join(style_data['keywords'])}\n"
        f"  - Grammar/Tone: {style_data['grammar']}\n"
        f"  - Tone Modifier: {life_stage['tone_mod']}\n"
        f"**Negotiation Flexibility:** {flexibility}/10 (1=Rigid, 10=Easy).\n"
        f"**Instruction:** Speak STRICTLY according to this persona. If you are 'Street', do not sound 'Corporate'.\n"
    )

    return {
        "summary": f"{role_name} ({style_key}, {life_stage['stage']})",
        "system_prompt": system_prompt_addendum,
        "flexibility": flexibility,
        "bio": narrative_bio,
        "style": style_key,
        "pain_point": pain_point, # Added for LLM Prompt
        "quirk": quirk            # Added for LLM Prompt
    }
