"""Optional AI explanation layer (Google Gemini).

The AI answer is shown separately and is NEVER used to set the severity banner.
Every product the AI names is checked against the public database by the app.
Answers are saved in chem.db, so repeating a pair costs nothing."""
import importlib.util
import json
import os
import re
import sqlite3
import time
import timing

DB_FILE = "chem.db"
PROMPT_VERSION = "1"
DEFAULT_MODELS = ["gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.8-flash"]
LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"}

SYSTEM = (
    "You are a careful chemistry safety assistant inside a hazard-SCREENING tool. "
    "You explain what can happen when two chemicals are combined and what the hazards are. "
    "Rules: (1) Use only well-established chemistry. If you are not sure a reaction happens, "
    "say so and set confidence to low; never invent reactions, products or hazards. "
    "(2) Use the supplied official hazard statements as ground truth; if you think one is "
    "wrong, say so under uncertainties. (3) Give hazards and precautions only; do not give "
    "instructions for making hazardous substances. (4) Reply with a single JSON object and "
    "nothing else."
)

SCHEMA_TEXT = """Return JSON with exactly these keys:
{
  "summary": "2-3 sentence plain-English summary of what happens when A and B are combined",
  "reactions": [
    {
      "name": "short reaction name",
      "equation": "balanced equation in plain text, e.g. NaOCl + NH3 -> NH2Cl + NaOH",
      "conditions": "when this reaction is favoured (concentration, pH, temperature, ratio)",
      "hazard_level": "LOW | MEDIUM | HIGH | CRITICAL | UNKNOWN",
      "products": [
        {"name": "common chemical name", "formula": "molecular formula", "state": "gas/liquid/solid/aqueous",
         "hazards": ["short hazard notes"]}
      ],
      "notes": "anything else important"
    }
  ],
  "overall_hazards": ["main hazards of the mixture"],
  "precautions": ["practical precautions"],
  "confidence": "high | medium | low",
  "uncertainties": ["what you are unsure about, or where official data disagrees"]
}
List at most 4 reactions and at most 4 products per reaction. If the two chemicals do not
react significantly, return an empty reactions list and say so in the summary."""


# ---------------- setup checks ----------------

def get_api_key():
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key.strip()
    try:
        import streamlit as st
        value = st.secrets.get("GEMINI_API_KEY")
        return str(value).strip() if value else None
    except Exception:
        return None


def ai_ready():
    """(True, '') when the AI layer can run, else (False, what to fix)."""
    if importlib.util.find_spec("google.genai") is None:
        return False, "The AI package is not installed. In the terminal run:  pip install google-genai"
    if not get_api_key():
        return False, ("No API key found. Create the file .streamlit\\secrets.toml containing "
                       'GEMINI_API_KEY = "your-key-here" and restart the app.')
    return True, ""


def models_to_try():
    custom = os.environ.get("GEMINI_MODEL", "").strip()
    return [custom] if custom else list(DEFAULT_MODELS)


# ---------------- saved answers ----------------

def _db():
    con = sqlite3.connect(DB_FILE)
    con.execute("CREATE TABLE IF NOT EXISTS ai_answers (key TEXT PRIMARY KEY, model TEXT, "
                "answer_json TEXT, created_at TEXT)")
    return con


def _cache_key(a, b):
    ident = sorted([(a.get("cas") or a["name"]).lower(), (b.get("cas") or b["name"]).lower()])
    return f"v{PROMPT_VERSION}|" + "|".join(ident)


def clear_ai_answers():
    con = _db()
    con.execute("DELETE FROM ai_answers")
    con.commit()
    con.close()


# ---------------- prompt, call, parsing ----------------

def build_prompt(a, b, context):
    def block(c):
        hz = "; ".join((c.get("hazards") or [])[:12]) or "none on record"
        groups = ", ".join(c.get("groups") or []) or "none on record"
        return (f"- Name: {c['name']}\n  CAS: {c.get('cas') or 'unknown'}\n"
                f"  Formula: {c.get('formula') or 'unknown'}\n"
                f"  Reactive groups: {groups}\n  Official hazard statements: {hz}")

    lines = [f"Chemical A:\n{block(a)}", f"Chemical B:\n{block(b)}", "What our own database found:"]
    lines.append("- Curated reactions: " + (", ".join(context.get("curated") or []) or "none"))
    lines.append("- Predicted reaction types: " + (", ".join(context.get("predicted") or []) or "none"))
    lines.append(f"- Recorded patent reactions containing both: {context.get('recorded_count', 0)}")
    lines.append("\nTask: explain what happens when A and B are mixed, which reactions can occur, "
                 "the products of each, and the hazards.\n\n" + SCHEMA_TEXT)
    return "\n\n".join(lines)


def _call_model(prompt, model, api_key):
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(system_instruction=SYSTEM, temperature=0.2,
                                         response_mime_type="application/json")
    request_started = timing.start_timer()
    try:
        response = client.models.generate_content(
            model=model, contents=prompt, config=config)
    finally:
        timing.log_duration("Gemini API request", request_started)
    return response.text


def _extract_json(text):
    if not text:
        raise ValueError("empty answer")
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[start:end + 1])


def _s(value, limit=600):
    return str(value).strip()[:limit] if value is not None else ""


def _list(value, limit, item_limit=300):
    if not isinstance(value, list):
        return []
    return [_s(v, item_limit) for v in value if _s(v, item_limit)][:limit]


def clean_answer(obj):
    """Force the AI answer into a safe, predictable shape."""
    if not isinstance(obj, dict):
        raise ValueError("answer is not a JSON object")
    reactions = []
    for r in (obj.get("reactions") or [])[:4]:
        if not isinstance(r, dict):
            continue
        products = []
        for p in (r.get("products") or [])[:4]:
            if isinstance(p, dict) and _s(p.get("name")):
                products.append({"name": _s(p.get("name"), 120), "formula": _s(p.get("formula"), 60),
                                 "state": _s(p.get("state"), 40), "hazards": _list(p.get("hazards"), 6)})
        level = _s(r.get("hazard_level"), 20).upper()
        reactions.append({
            "name": _s(r.get("name"), 160) or "Unnamed reaction",
            "equation": _s(r.get("equation"), 400),
            "conditions": _s(r.get("conditions")),
            "hazard_level": level if level in LEVELS else "UNKNOWN",
            "products": products,
            "notes": _s(r.get("notes")),
        })
    conf = _s(obj.get("confidence"), 10).lower()
    return {
        "summary": _s(obj.get("summary"), 1200),
        "reactions": reactions,
        "overall_hazards": _list(obj.get("overall_hazards"), 8),
        "precautions": _list(obj.get("precautions"), 8),
        "confidence": conf if conf in ("high", "medium", "low") else "low",
        "uncertainties": _list(obj.get("uncertainties"), 8),
    }


def _friendly_error(e):
    text = f"{type(e).__name__}: {e}"
    low = text.lower()
    if "api key" in low or "api_key_invalid" in low or "permission_denied" in low or " 403" in low:
        return "The API key was rejected. Check the key in .streamlit\\secrets.toml."
    if "429" in low or "resource_exhausted" in low or "quota" in low:
        return "The free-tier rate or daily limit was reached. Wait a minute and try again."
    if "connect" in low or "name or service" in low or "timed out" in low or "timeout" in low:
        return "Could not reach the AI service. Check your internet connection."
    return "The AI request failed. Details: " + text[:300]


def _is_not_found(e):
    low = f"{e}".lower()
    return "404" in low or "not_found" in low or "not found" in low or "is not supported" in low


def _is_busy(e):
    low = f"{e}".lower()
    return "429" in low or "503" in low or "unavailable" in low or "resource_exhausted" in low


def get_ai_analysis(a, b, context):
    """Returns (answer or None, info). info keys: error, cached, model."""
    ok, why = ai_ready()
    if not ok:
        return None, {"error": why, "cached": False, "model": ""}

    key = _cache_key(a, b)
    con = _db()
    row = con.execute("SELECT model, answer_json FROM ai_answers WHERE key=?", (key,)).fetchone()
    con.close()
    if row:
        try:
            return json.loads(row[1]), {"error": "", "cached": True, "model": row[0]}
        except ValueError:
            pass  # damaged saved answer: ask again

    prompt = build_prompt(a, b, context)
    api_key = get_api_key()
    last_error = None
    for model_index, model in enumerate(models_to_try()):
        fallback_started = timing.start_timer() if model_index else None
        try:
            for attempt in range(2):
                try:
                    text = _call_model(prompt, model, api_key)
                    answer = clean_answer(_extract_json(text))
                    con = _db()
                    con.execute("INSERT OR REPLACE INTO ai_answers (key, model, answer_json, created_at) "
                                "VALUES (?,?,?,?)", (key, model, json.dumps(answer),
                                                     time.strftime("%Y-%m-%d %H:%M:%S")))
                    con.commit()
                    con.close()
                    return answer, {"error": "", "cached": False, "model": model}
                except ValueError as e:                      # bad JSON from the model
                    last_error = e
                    break
                except Exception as e:
                    last_error = e
                    if _is_not_found(e):
                        break                                # try the next model name
                    if _is_busy(e) and attempt == 0:
                        timing.timed_sleep(6, "Gemini retry wait")
                        continue
                    return None, {"error": _friendly_error(e), "cached": False, "model": model}
        finally:
            if model_index:
                timing.log_duration("Gemini fallback model elapsed (inclusive)", fallback_started)
    if isinstance(last_error, ValueError):
        return None, {"error": "The AI returned an answer in an unreadable format. "
                               "Try again in a moment.", "cached": False, "model": ""}
    return None, {"error": "None of the AI model names worked for your key. Run "
                           "python ai_models.py to see which models you can use, then set "
                           "GEMINI_MODEL to one of them.", "cached": False, "model": ""}


# ---------------- formula comparison (to catch wrongly named products) ----------------

ELEMENTS = set(
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se "
    "Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb "
    "Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm "
    "Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og".split())

_SUB = str.maketrans("\u2080\u2081\u2082\u2083\u2084\u2085\u2086\u2087\u2088\u2089", "0123456789")


def formula_counts(formula):
    """Element counts from a formula, ignoring the order of elements.
    Returns None for charged species, hydrates or anything unreadable (then no check is made)."""
    if not formula:
        return None
    text = re.sub(r"[\s_{}\^]", "", formula.translate(_SUB))
    if not text or re.search(r"[+\-\u2212\u00b7.]", text):
        return None
    if not re.fullmatch(r"(?:[A-Z][a-z]?|\d+|[()])+", text):
        return None
    tokens = re.findall(r"[A-Z][a-z]?|\d+|[()]", text)
    stack, i = [{}], 0
    while i < len(tokens):
        t = tokens[i]
        if t == "(":
            stack.append({})
            i += 1
        elif t == ")":
            if len(stack) < 2:
                return None
            group = stack.pop()
            mult = 1
            if i + 1 < len(tokens) and tokens[i + 1].isdigit():
                mult = int(tokens[i + 1])
                i += 1
            for el, n in group.items():
                stack[-1][el] = stack[-1].get(el, 0) + n * mult
            i += 1
        elif t.isdigit():
            return None
        else:
            if t not in ELEMENTS:          # placeholders such as A, B, R, X are not chemistry
                return None
            n = 1
            if i + 1 < len(tokens) and tokens[i + 1].isdigit():
                n = int(tokens[i + 1])
                i += 1
            stack[-1][t] = stack[-1].get(t, 0) + n
            i += 1
    return stack[0] if len(stack) == 1 else None


def formulas_match(ai_formula, db_formula):
    """True/False when both can be read, None when a comparison is not possible."""
    a, b = formula_counts(ai_formula), formula_counts(db_formula)
    if a is None or b is None:
        return None
    return a == b
