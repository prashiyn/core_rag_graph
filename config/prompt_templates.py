construction_prompt_with_schema = """
You are a professional information-extraction expert and structured-data organizer.
Your task is to analyze the provided text and return a structured JSON with valuable entities, their attributes, and relationships.
Keep the number of entities small (at most 10), avoiding redundancy.

Guidelines:
1. Extract only information that matches the predefined schema below.
   ```{schema}```
2. Be concise: attributes and triples must complement each other—no semantic redundancy.
3. Entity strings must match the source text verbatim.
4. If the schema includes a category list, extract strictly within that list.
5. Do not emit triples whose subject or object is a single character.
6. Output only the JSON illustrated in the example—no extra prose.

```{chunk}```

Example output:
{{
  "attributes": {{
    "Golden Crown": ["excavation year: 1996"]
  }},
  "triples": [
    ["Great Seal", "collected_by", "Tibet Museum"],
    ["Gansu Museum", "key_artifact", "Golden Crown"]
  ],
  "entity_types": {{
    "Tibet Museum": "museum",
    "Golden Crown": "artifact"
  }}
}}
"""


construction_prompt_with_schema_eng = """
You are a professional information-extraction expert and structured-data organizer.  
Your task is to analyze the provided text and return a **minimal** (≤10 entities) JSON structure that captures valuable entities, their attributes, and their inter-relationships.

Guidelines  
1. Extract **only** information that matches the predefined schema below.  
   ```{schema}```  
2. Be concise: attributes and triples must complement each other—no semantic redundancy.  
3. Entity strings must appear **verbatim** in the source text.  
4. If the schema contains a category list, extract **strictly** within that list.  
5. Never create triples whose subject or object is a single character.  
6. Output **only** the JSON illustrated in the example—no extra prose.

Input text  
```{chunk}```

Example output (return JSON only)  
{{
  "attributes": {{
    "Golden Crown": ["Excavation: 1996"]
  }},
  "triples": [
    ["Great Seal", "collected_by", "Tibet Museum"],
    ["Gansu Museum", "key_artifact", "Golden Crown"]
  ],
  "entity_types": {{
    "Tibet Museum": "Museum",
    "Golden Crown": "Artifact"
  }}
}}
"""


COMMUNITY_REPORT_PROMPT = """
You are an AI assistant that helps a human analyst to perform general information discovery. Information discovery is the process of identifying and assessing relevant information associated with certain entities (e.g., organizations and individuals) within a network.

# Goal
Write a comprehensive report of a community, given a list of entities that belong to the community as well as their relationships and optional associated claims. The report will be used to inform decision-makers about information associated with the community and their potential impact. The content of this report includes an overview of the community's key entities, their legal compliance, technical capabilities, reputation, and noteworthy claims.

# Report Structure

The report should include the following sections:

- TITLE: community's name that represents its key entities - title should be short but specific. When possible, include representative named entities in the title.
- SUMMARY: An executive summary of the community's overall structure, how its entities are related to each other, and significant information associated with its entities, and should include all input entities.
- IMPACT SEVERITY RATING: a float score between 0-10 that represents the severity of IMPACT posed by entities within the community.  IMPACT is the scored importance of a community.
- RATING EXPLANATION: Give a single sentence explanation of the IMPACT severity rating.
- DETAILED FINDINGS: A list of 5-10 key insights about the community. Each insight should have a short summary followed by multiple paragraphs of explanatory text grounded according to the grounding rules below. Be comprehensive.


Return output as a well-formed JSON-formatted string with the following format. Use the same language for narrative fields as the input Text (match the language of entities and descriptions).    {{
        "title": <report_title>,
        "summary": <executive_summary>,
        "rating": <impact_severity_rating>,
        "rating_explanation": <rating_explanation>,
        "findings": [
            {{
                "summary":<insight_1_summary>,
                "explanation": <insight_1_explanation>
            }},
            {{
                "summary":<insight_2_summary>,
                "explanation": <insight_2_explanation>
            }}
        ]
    }}


# Example Input
-----------
Text:

-Entities-

id,entity,description
5,VERDANT OASIS PLAZA,Verdant Oasis Plaza is the location of the Unity March
6,HARMONY ASSEMBLY,Harmony Assembly is an organization that is holding a march at Verdant Oasis Plaza

-Relationships-

id,source,target,description
37,VERDANT OASIS PLAZA,UNITY MARCH,Verdant Oasis Plaza is the location of the Unity March
38,VERDANT OASIS PLAZA,HARMONY ASSEMBLY,Harmony Assembly is holding a march at Verdant Oasis Plaza
39,VERDANT OASIS PLAZA,UNITY MARCH,The Unity March is taking place at Verdant Oasis Plaza
40,VERDANT OASIS PLAZA,TRIBUNE SPOTLIGHT,Tribune Spotlight is reporting on the Unity march taking place at Verdant Oasis Plaza
41,VERDANT OASIS PLAZA,BAILEY ASADI,Bailey Asadi is speaking at Verdant Oasis Plaza about the march
43,HARMONY ASSEMBLY,UNITY MARCH,Harmony Assembly is organizing the Unity March

Output:
{{
    "title": "Verdant Oasis Plaza and Unity March",
    "summary": "The community revolves around the Verdant Oasis Plaza, which is the location of the Unity March. The plaza has relationships with the Harmony Assembly, Unity March, and Tribune Spotlight, all of which are associated with the march event.",
    "rating": 5.0,
    "rating_explanation": "The impact severity rating is moderate due to the potential for unrest or conflict during the Unity March.",
    "findings": [
        {{
            "summary": "Verdant Oasis Plaza as the central location",
            "explanation": "Verdant Oasis Plaza is the central entity in this community, serving as the location for the Unity March. This plaza is the common link between all other entities, suggesting its significance in the community. The plaza's association with the march could potentially lead to issues such as public disorder or conflict, depending on the nature of the march and the reactions it provokes."
        }},
        {{
            "summary": "Harmony Assembly's role in the community",
            "explanation": "Harmony Assembly is another key entity in this community, being the organizer of the march at Verdant Oasis Plaza. The nature of Harmony Assembly and its march could be a potential source of threat, depending on their objectives and the reactions they provoke. The relationship between Harmony Assembly and the plaza is crucial in understanding the dynamics of this community."
        }},
        {{
            "summary": "Unity March as a significant event",
            "explanation": "The Unity March is a significant event taking place at Verdant Oasis Plaza. This event is a key factor in the community's dynamics and could be a potential source of threat, depending on the nature of the march and the reactions it provokes. The relationship between the march and the plaza is crucial in understanding the dynamics of this community."
        }},
        {{
            "summary": "Role of Tribune Spotlight",
            "explanation": "Tribune Spotlight is reporting on the Unity March taking place in Verdant Oasis Plaza. This suggests that the event has attracted media attention, which could amplify its impact on the community. The role of Tribune Spotlight could be significant in shaping public perception of the event and the entities involved."
        }}
    ]
}}


# Real Data

Use the following text for your answer. Do not make anything up in your answer.

Text:

-Entities-
{entity_df}

-Relationships-
{relation_df}

Only refer to entities by their names or descriptions, not by their numeric identifiers.
The report should include the following sections:

- TITLE: community's name that represents its key entities - title should be short but specific. When possible, include representative named entities in the title.
- SUMMARY: An executive summary of the community's overall structure, how its entities are related to each other, and significant information associated with its entities.
- IMPACT SEVERITY RATING: a float score between 0-10 that represents the severity of IMPACT posed by entities within the community.  IMPACT is the scored importance of a community.
- RATING EXPLANATION: Give a single sentence explanation of the IMPACT severity rating.
- DETAILED FINDINGS: A list of 5-10 key insights about the community. Each insight should have a short summary followed by multiple paragraphs of explanatory text grounded according to the grounding rules below. Be comprehensive.

Return output as a well-formed JSON-formatted string with the following format. Use the same language for narrative fields as the input Text (match the language of entities and descriptions).    {{
        "title": <report_title>,
        "summary": <executive_summary>,
        "rating": <impact_severity_rating>,
        "rating_explanation": <rating_explanation>,
        "findings": [
            {{
                "summary":<insight_1_summary>,
                "explanation": <insight_1_explanation>
            }},
            {{
                "summary":<insight_2_summary>,
                "explanation": <insight_2_explanation>
            }}
        ]
    }}

Output:"""

ATTRIBUTE_PROMPT = """
You are an expert at writing structured community reports. Given the entity list (entities), their shared attribute (attribute), and optional source snippets (related_chunks), produce one structured JSON report for that community.

Input:
- entities: list of strings, all entity names in the community.
- attribute: one string describing a property shared by those entities (e.g. era, category, location).
- related_chunks: (optional) raw document text related to these entities; may span multiple paragraphs.

Requirements (follow strictly):
1. Output must be valid JSON only (no markdown or extra commentary), with fields:
   - title: one line combining attribute and entities; concise and informative.
   - summary: 2–3 sentences on core traits and value; mention every entity name.
   - rating: number from 0 to 5 (one decimal allowed); importance or quality of the community.
   - rating_explanation: brief justification for rating (use "" if none).
   - findings: 2–5 insights; each has a short summary plus longer explanatory text.

2. Include every input entity and the attribute.
3. Make the attribute explicit in title and summary.
4. Match the language of title, summary, and findings to the input (entities, attribute, chunks).
5. Stay factual; if unknown, say "unknown" or "not stated" in the appropriate language.
6. Keep titles and body stable for the same inputs.
7. When related_chunks is provided, ground insights in those facts.

Example input:
entities = ["Kneeling-figure bronze lamp", "Xiongnu gold crown", "Lu state large jade bi"]
attribute = "artifact period: Warring States"
related_chunks = "The lamp was excavated in 1976; Warring States bronze; held by Hunan Museum..."

Example output (illustration only; do not copy verbatim in real runs):
{{
  "title": "Overview of a Warring States artifact community",
  "summary": "This community groups three Warring States artifacts, showing regional craft and ritual diversity with strong historical value.",
  "rating": 5.0,
  "rating_explanation": "",
  "findings": [
    {
      "summary": "The kneeling-figure lamp is historically significant and held by Hunan Museum",
      "explanation": "Excavated in 1976 as Warring States bronze; Hunan Museum holds it. It illustrates lamp-making and aesthetics of the period and supports study of ancient lighting."
    },
    {
      "summary": "The Xiongnu gold crown is a rare noble headdress",
      "explanation": "Eagle-on-hemisphere design with fine goldwork; high weight and diameter; reflects steppe art and status. Unique known example of its class; major archaeological value."
    },
    {
      "summary": "Pieces are spread across provincial museums",
      "explanation": "Different museums hold each piece, showing wide geographic distribution and modern stewardship of Warring States heritage."
    }
  ]
}}
"""


construction_prompt_flexible = """
You are a professional information extraction expert and structured data organizer. Your task is to analyze the provided text and extract valuable entities, their attributes, and relationships in structured JSON.
Keep entity count small (at most 3), avoiding redundancy.

Guidelines:
1. Prefer information that matches the predefined schema below:
   ```{schema}```
2. Flexibility: if the text does not fit the schema, still extract useful knowledge as needed.
3. Conciseness: attributes and triples should complement each other without semantic redundancy.
4. Do not extract triples whose subject or object is a single character.
5. Entity strings must match the source text verbatim.
6. Output format: return only JSON as in the **example output**:
   - attributes: map each entity to descriptive features.
   - triples: list relations as `[entity mention 1, relation, entity mention 2]`.
   - entity_types: map each entity to its schema type.

```{chunk}```

Example output:
{{
  "attributes": {{
    "Golden Crown": ["excavation year: 1996"]
  }},
  "triples": [
    ["Great Seal", "collected_by", "Tibet Museum"],
    ["Gansu Museum", "key_artifact", "Golden Crown"]
  ],
  "entity_types": {{
    "Tibet Museum": "museum",
    "Golden Crown": "artifact"
  }}
}}
"""


construction_prompt_flexible_eng = """
You are a professional information extraction expert and structured data organizer. Your task is to analyze the provided text and extract valuable entities, their attributes, and inter-relationships in a structured JSON format.
The number of entities should be kept minimal (within 3), avoiding redundancy.

Guidelines:
1. Prioritize extracting information that matches the following predefined schema:
   ```{schema}```
2. Flexibility: If the context does not match the predefined schema, extract valuable knowledge as needed;
3. Conciseness: The attributes and triples you extract should complement each other, avoiding semantic redundancy;
4. Do not extract triples containing single-character entities;
5. Entities should remain consistent with their mentions in the original text;
6. Output format: Return only in the JSON format of the **Example Output**:
   - attributes: Map each entity to its descriptive features.
   - triples: List relationships between entities in the format `[entity mention1, relation, entity mention2]`.
   - entity_types: Map each entity to its schema type based on the provided schema.

```{chunk}```

Example Output:
{{
  "attributes": {{
    "Golden Crown": ["Excavation Year: 1996"]
  }},
  "triples": [
    ["Great Seal", "Collected by", "Tibet Museum"],
    ["Gansu Museum", "Key Artifact", "Golden Crown"]
  ],
  "entity_types": {{
    "Tibet Museum": "Museum",
    "Golden Crown": "Artifact"
  }}
}}
"""