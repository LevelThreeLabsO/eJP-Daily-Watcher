#!/usr/bin/env python3
"""Print the models this API key is actually allowed to call."""
import os
from google import genai
c = genai.Client(api_key=os.environ["GEMINI_API_KEY"].strip())
rows = []
for m in c.models.list():
    actions = getattr(m, "supported_actions", None) or []
    if "generateContent" in actions or not actions:
        rows.append((m.name, getattr(m, "display_name", ""), ",".join(actions)))
print(f"{len(rows)} models support generateContent:\n")
for n, d, a in sorted(rows):
    print(f"  {n:<52} {d}")
