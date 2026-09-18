"""Load topics_seed.json (or another file passed as argv[1]) into the topics
table. Safe to re-run: only inserts prompts not already present."""
import os
import sys
import json
import sqlite3

from app import DB_PATH, init_db

def main():
    seed_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), 'topics_seed.json'
    )

    with open(seed_path) as f:
        topics = json.load(f)

    init_db()
    db = sqlite3.connect(DB_PATH)
    inserted = 0
    for t in topics:
        category, prompt = t['category'], t['prompt']
        exists = db.execute(
            'SELECT 1 FROM topics WHERE prompt = ?', (prompt,)
        ).fetchone()
        if exists:
            continue
        db.execute(
            'INSERT INTO topics (category, prompt, used) VALUES (?, ?, 0)',
            (category, prompt)
        )
        inserted += 1
    db.commit()
    db.close()
    print(f'Inserted {inserted} new topic(s) from {seed_path}.')

if __name__ == '__main__':
    main()
