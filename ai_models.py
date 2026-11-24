"""Lists the Gemini models your API key can use for text answers.
Run:  python ai_models.py
Then, if the app cannot find a model, set GEMINI_MODEL to one of the names shown
(for example:  $env:GEMINI_MODEL="gemini-2.5-flash"  before starting the app)."""
from ai_explain import ai_ready, get_api_key

ok, why = ai_ready()
if not ok:
    raise SystemExit(why)

from google import genai

client = genai.Client(api_key=get_api_key())
names = []
for m in client.models.list():
    actions = getattr(m, "supported_actions", None) or []
    if "generateContent" in actions:
        names.append(m.name.replace("models/", ""))

if not names:
    raise SystemExit("No text models were returned for this key.")

names.sort(key=lambda n: (0 if "flash" in n and "lite" not in n else 1, n))
print("Models your key can use (best first for the free tier):")
for n in names:
    print("  ", n)
