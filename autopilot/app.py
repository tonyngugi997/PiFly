import os
import time
import hmac
import hashlib
import base64
import logging
import sqlite3
import urllib.request
from functools import wraps

from flask import Flask, request, jsonify, render_template, abort, g

DB_PATH = os.environ.get(
    'AUTOPILOT_DB_PATH', os.path.join(os.path.dirname(__file__), 'autopilot.db')
)
N8N_SECRET = os.environ.get('N8N_SECRET')
HMAC_SECRET = os.environ.get('HMAC_SECRET')
TOKEN_EXPIRY_SECONDS = 24 * 60 * 60

if not N8N_SECRET or not HMAC_SECRET:
    raise RuntimeError('N8N_SECRET and HMAC_SECRET must be set in the environment')

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 256 * 1024  # drafts are short text

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('autopilot')


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS topics (
            id         INTEGER PRIMARY KEY,
            category   TEXT NOT NULL,
            prompt     TEXT NOT NULL,
            used       INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS drafts (
            id              INTEGER PRIMARY KEY,
            topic_id        INTEGER NOT NULL REFERENCES topics(id),
            category        TEXT NOT NULL,
            body            TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'pending',
            token           TEXT NOT NULL UNIQUE,
            token_expiry    INTEGER NOT NULL,
            n8n_resume_url  TEXT,
            created_at      INTEGER NOT NULL,
            posted_at       INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_topics_unused ON topics(used, id);
        CREATE INDEX IF NOT EXISTS idx_drafts_token ON drafts(token);
    ''')
    db.commit()
    db.close()


def require_bearer(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            abort(401)
        supplied = auth[len('Bearer '):]
        if not hmac.compare_digest(supplied, N8N_SECRET):
            abort(401)
        return fn(*args, **kwargs)
    return wrapper


def sign_token(draft_id, expiry):
    msg = f'{draft_id}.{expiry}'.encode()
    sig = hmac.new(HMAC_SECRET.encode(), msg, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode().rstrip('=')
    return f'{draft_id}.{expiry}.{sig_b64}'


def verify_token(token):
    """Returns draft_id if the token's signature is valid and it hasn't
    expired, else None. Never trusts draft_id/expiry without checking the
    signature over the exact string first."""
    parts = token.split('.')
    if len(parts) != 3:
        return None
    draft_id_s, expiry_s, _sig_b64 = parts
    if not draft_id_s.isdigit() or not expiry_s.isdigit():
        return None
    expected = sign_token(draft_id_s, expiry_s)
    if not hmac.compare_digest(expected, token):
        return None
    if int(expiry_s) < int(time.time()):
        return None
    return int(draft_id_s)


# In-memory rate limiting is fine here: single Flask process, single user, low volume.
_rate_buckets = {}

def rate_limited(key, limit, window_seconds):
    now = time.time()
    bucket = _rate_buckets.setdefault(key, [])
    bucket[:] = [t for t in bucket if now - t < window_seconds]
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


def _get_draft_or_404(draft_id):
    db = get_db()
    row = db.execute('SELECT * FROM drafts WHERE id = ?', (draft_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _resolve_draft_from_token(token):
    """Shared by preview/approve/reject: verifies signature+expiry, then
    cross-checks against the stored token so a draft can be invalidated
    server-side even before natural expiry."""
    draft_id = verify_token(token)
    if draft_id is None:
        abort(404)
    draft = _get_draft_or_404(draft_id)
    if not hmac.compare_digest(draft['token'], token):
        abort(404)
    return draft


def _notify_n8n(resume_url, approved):
    if not resume_url:
        return
    try:
        urllib.request.urlopen(
            f'{resume_url}?approved={"true" if approved else "false"}', timeout=5
        )
    except Exception:
        log.exception('failed to resume n8n webhook')


@app.route('/api/next-topic', methods=['GET'])
@require_bearer
def next_topic():
    db = get_db()
    row = db.execute(
        'SELECT id, category, prompt FROM topics WHERE used = 0 ORDER BY id LIMIT 1'
    ).fetchone()
    if row is None:
        return jsonify({'error': 'no unused topics'}), 404
    db.execute('UPDATE topics SET used = 1 WHERE id = ?', (row['id'],))
    db.commit()
    return jsonify({'topic_id': row['id'], 'category': row['category'], 'prompt': row['prompt']})


@app.route('/api/drafts', methods=['POST'])
@require_bearer
def create_draft():
    data = request.get_json(silent=True) or {}
    topic_id = data.get('topic_id')
    category = data.get('category')
    body = data.get('body')
    n8n_resume_url = data.get('n8n_resume_url')

    if not isinstance(topic_id, int) or not category or not body:
        return jsonify({'error': 'topic_id (int), category, body are required'}), 400

    db = get_db()
    now = int(time.time())
    expiry = now + TOKEN_EXPIRY_SECONDS

    cur = db.execute(
        '''INSERT INTO drafts (topic_id, category, body, status, token, token_expiry,
                                n8n_resume_url, created_at)
           VALUES (?, ?, ?, 'pending', '', ?, ?, ?)''',
        (topic_id, category, body, expiry, n8n_resume_url, now)
    )
    draft_id = cur.lastrowid
    token = sign_token(draft_id, expiry)
    db.execute('UPDATE drafts SET token = ? WHERE id = ?', (token, draft_id))
    db.commit()

    preview_url = f'{request.url_root.rstrip("/")}/preview/{token}'
    return jsonify({'draft_id': draft_id, 'token': token, 'preview_url': preview_url}), 201


@app.route('/preview/<token>', methods=['GET'])
def preview(token):
    draft = _resolve_draft_from_token(token)
    return render_template('preview.html', draft=draft, token=token)


@app.route('/api/approve/<token>', methods=['POST'])
def approve(token):
    if rate_limited(f'approve:{request.remote_addr}', limit=5, window_seconds=60):
        abort(429)
    draft = _resolve_draft_from_token(token)
    if draft['status'] != 'pending':
        return jsonify({'status': draft['status']}), 409

    db = get_db()
    db.execute("UPDATE drafts SET status = 'approved' WHERE id = ?", (draft['id'],))
    db.commit()
    _notify_n8n(draft['n8n_resume_url'], approved=True)
    return jsonify({'status': 'approved'})


@app.route('/api/reject/<token>', methods=['POST'])
def reject(token):
    if rate_limited(f'reject:{request.remote_addr}', limit=5, window_seconds=60):
        abort(429)
    draft = _resolve_draft_from_token(token)
    if draft['status'] != 'pending':
        return jsonify({'status': draft['status']}), 409

    db = get_db()
    db.execute("UPDATE drafts SET status = 'rejected' WHERE id = ?", (draft['id'],))
    db.commit()
    _notify_n8n(draft['n8n_resume_url'], approved=False)
    return jsonify({'status': 'rejected'})


@app.route('/api/drafts/<int:draft_id>/posted', methods=['POST'])
@require_bearer
def mark_posted(draft_id):
    _get_draft_or_404(draft_id)
    db = get_db()
    db.execute(
        "UPDATE drafts SET status = 'posted', posted_at = ? WHERE id = ?",
        (int(time.time()), draft_id)
    )
    db.commit()
    return jsonify({'status': 'posted'})


@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; object-src 'none'; base-uri 'self'; "
        "form-action 'self'; frame-ancestors 'none'"
    )
    return response


if __name__ == '__main__':
    init_db()
    debug_mode = os.environ.get('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes')
    app.run(debug=debug_mode, host='127.0.0.1', port=int(os.environ.get('AUTOPILOT_PORT', 8001)))
