"""Backend filling of the database for a new chemical pair.

Asks the backend service for an answer, runs the automatic checks and saves the result.
Nothing here is shown on the page: problems are only printed in the terminal window."""
import ai_explain
import db
from ai_verify import decide_status, verify_answer
from pair_store import save_pair_answer


def fill_pair(a, b, context):
    """a, b: dicts with name, cas, formula, hazards, groups.
    Returns {'answer': ..., 'info': ..., 'saved': counts or None}."""
    answer, info = ai_explain.get_ai_analysis(a, b, context)
    if answer is None:
        print(f"[backend] could not create entries for {a['name']} + {b['name']}: {info['error']}")
        return {"answer": None, "info": info, "saved": None}
    if not answer["reactions"]:
        print(f"[backend] no reaction reported for {a['name']} + {b['name']}; nothing saved")
        return {"answer": answer, "info": info, "saved": None}
    checks = verify_answer(answer, lambda name: db.get_product("name", name))
    decisions = decide_status(answer, checks)
    saved = save_pair_answer(a, b, answer, checks, decisions, info["model"])
    print(f"[backend] saved {a['name']} + {b['name']}: {saved['checked']} auto-checked, "
          f"{saved['review']} need review")
    return {"answer": answer, "info": info, "saved": saved}
