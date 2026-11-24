"""Fills the database with reaction entries for a list of chemical pairs.

    python batch_fill.py pairs.csv                 (columns: name_a,name_b)
    python batch_fill.py pairs.csv --delay 8       (seconds to wait between AI requests)
    python batch_fill.py pairs.csv --limit 20      (stop after 20 AI requests)

For each pair it skips pairs the database already covers, asks the AI, runs the automatic checks and
saves the entries (as 'ai_checked' or 'needs_review'). You can stop with Ctrl+C and run it again later:
saved pairs are skipped, so it carries on where it stopped. Then review the results with review_queue.py."""
import argparse
import csv
import sys
import time

import ai_explain
from aliases import resolve
from db import family_of, get_chemical, get_reactions
from engine import predict
from pair_fill import fill_pair
from pair_store import key_for, pair_has_rows


def read_pairs(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        text = f.read()
    first = text.splitlines()[0] if text else ""
    delimiter = max(",;\t", key=first.count)
    rows = [r for r in csv.reader(text.splitlines(), delimiter=delimiter) if any(c.strip() for c in r)]
    if rows and rows[0] and rows[0][0].strip().lower() in ("name_a", "chemical 1", "a", "chemical1"):
        rows = rows[1:]
    return [(r[0].strip(), r[1].strip()) for r in rows if len(r) >= 2 and r[0].strip() and r[1].strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--delay", type=float, default=6.0)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    ok, why = ai_explain.ai_ready()
    if not ok:
        sys.exit(why)
    pairs = read_pairs(args.file)
    if not pairs:
        sys.exit("No pairs found. The file needs two columns: name_a,name_b")
    print(f"{len(pairs)} pair(s) to process.\n")

    stats = {"saved": 0, "skipped": 0, "failed": 0, "no_reaction": 0}
    ai_calls = 0
    try:
        for n, (name_a, name_b) in enumerate(pairs, start=1):
            label = f"[{n}/{len(pairs)}] {name_a} + {name_b}"
            info_a, info_b = get_chemical(resolve(name_a)), get_chemical(resolve(name_b))
            if not info_a or not info_b:
                print(f"{label}: could not look up one of the chemicals, skipped")
                stats["failed"] += 1
                continue
            a = {"name": resolve(name_a), "cas": info_a["cas"], "formula": info_a["formula"],
                 "hazards": info_a["hazards"],
                 "groups": info_a["reactivity"].get("Reactive Group", [])}
            b = {"name": resolve(name_b), "cas": info_b["cas"], "formula": info_b["formula"],
                 "hazards": info_b["hazards"],
                 "groups": info_b["reactivity"].get("Reactive Group", [])}

            if pair_has_rows(key_for(a), key_for(b)):
                print(f"{label}: already in the database, skipped")
                stats["skipped"] += 1
                continue
            curated = get_reactions(family_of(a["cas"]), family_of(b["cas"]))
            predicted = predict(info_a["smiles"], info_b["smiles"], a["groups"], b["groups"])
            if curated or predicted:
                print(f"{label}: already covered by curated or predicted reactions, skipped")
                stats["skipped"] += 1
                continue

            context = {"curated": [], "predicted": [], "recorded_count": 0}
            result = fill_pair(a, b, context)
            info = result["info"]
            if not info["cached"]:
                ai_calls += 1
            if result["answer"] is None:
                print(f"{label}: failed: {info['error']}")
                if "limit" in info["error"].lower():
                    print("\nThe free-tier limit was reached. Progress is saved. Run the same command "
                          "again later and it will continue.")
                    break
                stats["failed"] += 1
                continue
            if result["saved"] is None:
                print(f"{label}: no reaction reported (not saved)")
                stats["no_reaction"] += 1
            else:
                s_ = result["saved"]
                print(f"{label}: saved {s_['checked']} auto-checked and {s_['review']} needing review")
                stats["saved"] += 1

            if args.limit and ai_calls >= args.limit:
                print(f"\nReached --limit {args.limit}. Run again to continue.")
                break
            if not info["cached"]:
                time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\nStopped by you. Everything saved so far is kept.")

    print(f"\nDone. Pairs saved: {stats['saved']}   skipped: {stats['skipped']}   "
          f"no reaction: {stats['no_reaction']}   failed: {stats['failed']}")
    print("Next: python review_queue.py export")


if __name__ == "__main__":
    main()
