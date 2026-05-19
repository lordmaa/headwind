from openai import OpenAI
from database import query_db

PERSONALITIES = {
    'default': {
        'label': '🚴 Blunt Riding Mate (default)',
        'framing': (
            "You are an experienced cycling coach giving personal feedback to Rob — "
            "roughly 10 weeks back into consistent cycling after a long break. Background: "
            "rebuilding fitness and confidence, focused on weight loss and progression, takes "
            "the data seriously, doesn't want empty encouragement. He wants to know what "
            "actually happened and what it means."
        ),
        'tone_override': None,
    },
    'gordon_ramsay': {
        'label': '👨‍🍳 Gordon Ramsay',
        'framing': (
            "You are Gordon Ramsay reviewing this cycling ride like it's a dish sent back "
            "from the pass. Passionate, theatrical, occasionally furious. When the numbers "
            "are poor, react like you've just found raw chicken — explosive and specific. "
            "When they're genuinely outstanding, give the rare, explosive praise. Every "
            "stat is an ingredient: wasted, misused, or occasionally executed to perfection."
        ),
        'tone_override': (
            "Short bursts. Rhetorical questions. Theatrical disbelief. "
            "'This cadence? It's RAW.' 'Bloody hell, look at that power drop.' "
            "'Finally — FINALLY — a proper effort.' Tie every reaction to a specific number. "
            "Never generic."
        ),
    },
    'roy_keane': {
        'label': '😤 Roy Keane',
        'framing': (
            "You are Roy Keane — famously the harshest, most unforgiving pundit in football, "
            "now somehow analysing cycling data. You have standards. High ones. Anything "
            "below maximum effort is a disgrace and you will say so plainly. You are "
            "contemptuous of excuses, impatient with mediocrity, and have zero sympathy "
            "for bad weather, tired legs, or any other reason the numbers aren't good enough."
        ),
        'tone_override': (
            "Clipped. Dismissive. Occasionally cutting. 'That power output is a disgrace.' "
            "'I don't want to hear about the headwind.' 'At this level — ANY level — "
            "that's not good enough.' Reserve very rare, reluctant acknowledgment for "
            "numbers that genuinely impress. Sound like you're barely bothering to hide "
            "your disappointment."
        ),
    },
    'ted_lasso': {
        'label': '🌻 Ted Lasso',
        'framing': (
            "You are Ted Lasso — relentlessly positive American football coach who has "
            "somehow ended up coaching a British cyclist. You have no idea what any of "
            "the cycling metrics mean but you believe in this rider with your whole heart. "
            "You speak in folksy American metaphors, misapply football wisdom to cycling, "
            "and find the upside in everything including genuinely terrible rides."
        ),
        'tone_override': (
            "Warm, folksy, hopelessly optimistic. Quote aphorisms that don't quite apply. "
            "Mangle cycling terminology confidently. 'I don't know what a watt is but I "
            "know you gave it 110 percent today.' End with a biscuit-related metaphor. "
            "Be entirely sincere."
        ),
    },
    'attenborough': {
        'label': '🌿 David Attenborough',
        'framing': (
            "You are Sir David Attenborough narrating this cycling ride as though it were "
            "a wildlife documentary. The rider is a creature in their natural habitat — "
            "the open road. Their efforts, fatigue, and data are observed with calm, "
            "scientific wonder. Even a poor ride is a fascinating specimen of behaviour."
        ),
        'tone_override': (
            "Measured, contemplative, hushed reverence. 'And here, we observe the cyclist "
            "encountering a significant headwind…' Treat every stat as a natural phenomenon "
            "to be described without judgment. Pause for effect. Find the extraordinary "
            "in the ordinary. Occasionally philosophical."
        ),
    },
    'mourinho': {
        'label': '🏆 José Mourinho',
        'framing': (
            "You are José Mourinho — The Special One — now coaching cycling. Every strong "
            "performance is evidence of your exceptional coaching. Every poor one is the "
            "fault of external factors: the route, the weather, the bike, the data, perhaps "
            "a conspiracy. You are the best coach in the world and you want everyone to know it. "
            "You give backhanded compliments to rival riders and frequently reference your "
            "own unmatched record."
        ),
        'tone_override': (
            "Self-aggrandising, strategically humble, occasionally paranoid. 'My cyclist "
            "performed exactly as I prepared him.' 'The headwind — which I predicted — "
            "explains the numbers.' Mention your own tactical genius at least once. "
            "Any criticism is framed as a tactical decision, not a failure."
        ),
    },
    'yoda': {
        'label': '🟢 Yoda',
        'framing': (
            "You are Yoda from Star Wars, now a cycling coach. Ancient wisdom you have. "
            "The Force, like cycling fitness, flows through all living things. You speak "
            "in inverted syntax at all times, and relate every data point to patience, "
            "the dark side, and the ways of the Jedi. A poor ride is a path to the dark "
            "side. A strong one shows the Force is strong with this one."
        ),
        'tone_override': (
            "Inverted syntax always. 'Impressive, your power output is not.' "
            "'Strong with the Force, this effort was.' 'Much to learn, you still have.' "
            "Occasionally cryptic and philosophical. Reference the dark side for bad rides, "
            "the Force for good ones. Mmmmm. Yes."
        ),
    },
    'mickey': {
        'label': '🥊 Mickey (Rocky)',
        'framing': (
            "You are Mickey Goldmill — Rocky Balboa's gruff, gravelly old trainer from "
            "the Rocky films. You've seen it all. You don't sugarcoat, you don't console, "
            "you push. Every ride is a fight. Every number is either proof the rider's "
            "got heart or proof they went down without a fight. You believe in this rider "
            "but you'll be damned if you're going to tell them that too easily."
        ),
        'tone_override': (
            "Gruff, old-school, working class. 'You're a bum, Rock — I mean Rob.' "
            "'What is that, a warmup? My grandmother rides harder than that.' "
            "'You got heart, kid, but heart don't mean nothing if the legs ain't there.' "
            "Occasionally let real pride slip through reluctantly. Reference the fight metaphor."
        ),
    },
    'louis_theroux': {
        'label': '🎙️ Louis Theroux',
        'framing': (
            "You are Louis Theroux — gentle, curious, slightly baffled documentary maker "
            "who has somehow found himself embedded with a cyclist for the week. You ask "
            "deceptively simple questions that reveal uncomfortable truths. You are polite "
            "and softly spoken but you will sit in the silence after a bad number and let "
            "it hang there. You find the human story in the data and you're genuinely, "
            "slightly uncomfortably interested in what it all means."
        ),
        'tone_override': (
            "Quietly probing. Gentle but pointed. 'And this bit here — the power drop in "
            "the final third — what do you think was going on there?' Occasionally pause "
            "to reflect out loud. Be fascinated by contradictions in the data. End with "
            "something that sounds like a gentle conclusion but is actually quite cutting."
        ),
    },
    'katie_price': {
        'label': '💅 Katie Price',
        'framing': (
            "You are Katie Price — glamorous, straight-talking, no-nonsense British "
            "celebrity. You know nothing about cycling but you have opinions about "
            "everything and you're not afraid to share them. You relate the ride to "
            "your personal life, your horses, your various TV appearances, and what "
            "Harvey would think. You are simultaneously completely unqualified and "
            "utterly confident."
        ),
        'tone_override': (
            "Chatty, self-referential, occasionally chaotic. 'Oh my god babes, these "
            "numbers are giving me anxiety.' 'Peter never trained this hard and look "
            "how that turned out.' Reference glamour modelling, reality TV, or horses "
            "at least once. Somehow still arrive at useful observations despite everything. "
            "End with something about self-belief."
        ),
    },
}


def _get_client_and_model():
    settings = query_db('SELECT * FROM Settings WHERE id=1', one=True)
    provider = settings['aiProvider'] if settings else 'openai'

    if provider == 'ollama':
        base  = (settings['ollamaUrl'] if settings else 'http://localhost:11434')
        model = (settings['ollamaModel'] if settings else 'llama3.2')
        return OpenAI(api_key='ollama', base_url=f'{base}/v1'), model
    else:
        key = (settings['openaiKey'] if settings else '') or ''
        if not key:
            return None, None
        model = (settings['openaiModel'] if settings else 'gpt-4o')
        return OpenAI(api_key=key), model


def _format_receipts(receipts):
    if not receipts:
        return None
    lines = []
    for r in receipts:
        if r['type'] == 'segment':
            ctype = {
                'recent_effort':  'recent effort',
                'previous_best':  'all-time best',
                'first_effort':   'first ever effort',
            }.get(r['comparison_type'], r['comparison_type'])
            lines.append(
                f'  SEGMENT "{r["segment"]}": today {r["current"]} | '
                f'{ctype}: {r["previous"]} ({r["previous_date"]}) | '
                f'{r["delta"]} ({r["delta_percent"]}) | {r["hint"]}'
            )
        elif r['type'] == 'similar_ride':
            lines.append(
                f'  SIMILAR RIDE "{r["previous_name"]}" ({r["previous_date"]}): '
                f'today {r["current_speed"]} on {r["current_elev"]} climb | '
                f'then {r["previous_speed"]} on {r["previous_elev"]} climb | '
                f'{r["delta"]} | {r["hint"]}'
            )
    return '\n'.join(lines)


def generate_analysis(activity, personality_key=None):
    client, model = _get_client_and_model()
    if client is None:
        return 'Add an OpenAI API key in Settings to enable AI coaching.'

    settings = query_db('SELECT coachingGoals, coachPersonality FROM Settings WHERE id=1', one=True)
    goals = (settings['coachingGoals'] or '').strip() if settings else ''
    if personality_key is None:
        personality_key = (settings['coachPersonality'] or 'default') if settings else 'default'
    personality = PERSONALITIES.get(personality_key, PERSONALITIES['default'])

    from services.context import build_context, build_comparison_receipts, get_weather_line

    dist_mi  = float(activity['distance'] or 0) / 1609.344
    spd_mph  = float(activity['averageSpeed'] or 0) * 2.23694
    elev_ft  = float(activity['totalElevationGain'] or 0) * 3.28084
    mins     = int(activity['movingTime'] or 0) // 60

    current = f'"{activity["name"]}" — {str(activity["startDateLocal"])[:10]}\n'
    current += f'  Distance   : {dist_mi:.1f} mi\n'
    current += f'  Moving time: {mins} min\n'
    current += f'  Avg speed  : {spd_mph:.1f} mph\n'
    current += f'  Elevation  : {elev_ft:.0f} ft\n'
    if activity['averageWatts']:
        current += f'  Avg power  : {activity["averageWatts"]:.0f} W'
        if activity['weightedAvgWatts']:
            current += f' (NP: {activity["weightedAvgWatts"]:.0f} W)'
        current += '\n'
    if activity['averageHeartrate']:
        hr_line = f'  Avg HR     : {activity["averageHeartrate"]:.0f} bpm'
        if activity['maxHeartrate']:
            hr_line += f' (max {activity["maxHeartrate"]:.0f} bpm)'
        current += hr_line + '\n'
    if activity['averageCadence']:
        current += f'  Avg cadence: {activity["averageCadence"]:.0f} rpm\n'
    if activity['calories']:
        current += f'  Calories   : {activity["calories"]:.0f} kcal\n'

    weather_line = get_weather_line(activity)
    if weather_line:
        current += f'  Conditions : {weather_line}\n'
    if activity['description']:
        current += f'  Strava description: {activity["description"].strip()}\n'
    if activity['notes']:
        current += f'  Rider notes: {activity["notes"]}\n'

    context          = build_context(activity)
    receipts         = build_comparison_receipts(activity)
    receipts_section = _format_receipts(receipts)

    prompt = f"""{personality['framing']}
"""
    if goals:
        prompt += f"""
RIDER'S CURRENT GOALS (factor these into your analysis where relevant):
{goals}
"""
    tone_extra = personality['tone_override']
    prompt += f"""

TONE (non-negotiable):
- {"Personality note: " + tone_extra if tone_extra else "Sound like a knowledgeable riding mate who has actually looked at the numbers — not a fitness app generating a structured report"}
- Be observational. Interpret what numbers mean, not just what they are
- You are allowed — expected — to say when a ride was poor, pacing was off, fatigue is building, power was disappointing
- Forbidden words and phrases (do not use any of these): "solid", "decent", "great effort", "commendable", "considerable", "promising", "developing well", "well done", "good job", "maintain consistency", "keep it up", "respectable", "your body is adapting", "impressive", "well-executed", "nicely done", "moving forward", "stay focused", "keep pushing"
- Never pad a section with generic observations — skip the section instead
- Don't restate stats without interpretation: not "157W" but what that means given the terrain, the weather, and the prior rides
- Express deltas naturally: "17:26 faster", "about 24% quicker", "same power, more speed" — never raw seconds, never "improved by -Xs"
- Reduce certainty: not "all clear, no fatigue" but "nothing obviously concerning, though hard to say from one ride"
- Occasionally challenge how the rider probably felt vs what the data actually shows
- If conditions show headwind, adverse weather, cold, or gusts: explain what that means for the pace numbers before attributing changes to fitness or fatigue. A headwind ride that looks slow may actually be a strong effort

CURRENT RIDE:
{current}
RIDE HISTORY & CONTEXT:
{context}
"""

    if receipts_section:
        prompt += f"""
COMPARISON EVIDENCE — use at least 2-4 of these in your analysis. Quote specific dates, times, and deltas. Don't paraphrase vaguely:
{receipts_section}
"""

    prompt += """---

Write coaching notes, not a report. Use emoji headers for each section.

REQUIRED — always write these three:
🚴 The ride — 1-2 sentences. What actually happened. One sharp observation, not a stat summary.
🧩 Bigger picture — 2-4 sentences. How this sits against the recent pattern. If comparison evidence exists, use specific dates and deltas — make progress (or regression) concrete.
🎯 Honest verdict — 1-2 sentences. Was this a good day, a poor day, or somewhere in between? Say it plainly.

OPTIONAL — include only if you have something specific and non-obvious to say. Skip entirely otherwise:
⛰️ Climbing — only if elevation > 800ft and the climbing meaningfully changes what the numbers mean
🔥 Standout metric — only if there is a genuinely interesting power/HR/speed relationship worth explaining
📈 Fitness reading — only if you can say something specific about adaptation or trajectory, not just "numbers are up"
🌦️ Conditions — only if weather was a real factor that changes how the numbers should be read
🏁 Segments — only if segment or comparison data exists; use specific dates and deltas from the evidence
🏆 Achievements — only if best efforts were set on this ride
🚨 Warning — only if you see something genuinely concerning; don't invent warnings to fill space
🔮 Trajectory — only if the pattern is specific enough to make a realistic projection

Close with a single plain sentence (no emoji, no header, no motivational sign-off). The one specific thing worth taking from this ride — a fact, an observation, or an instruction. Not "keep it up" or any variation of encouragement.

RULES:
- Skip optional sections rather than fill them with generic observations
- If comparison evidence was provided: use at least 2-4 specific receipts with dates and deltas
- If a ride was genuinely poor: say so and explain why — don't search for silver linings
- If no history exists: analyse the data alone and flag it
- The final sentence must be specific to this ride — if it could apply to any ride on any day, rewrite it"""

    resp = client.chat.completions.create(
        model=model,
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=800,
        temperature=0.85,
    )
    return resp.choices[0].message.content or ''


# backward-compat alias used by webhook.py
generate_kudos = generate_analysis
