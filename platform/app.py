from datetime import timedelta
from functools import wraps
import hashlib, os, sqlite3, time, urllib.parse, uuid

from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

from lti import (generate_key_pair, make_id_token, public_key_to_jwk,
                 verify_tool_jwt)

app = Flask(__name__)
app.secret_key = 'platform-demo-secret-change-in-prod'
app.permanent_session_lifetime = timedelta(days=7)
app.config['SESSION_COOKIE_NAME'] = 'platform_session'

DATABASE = os.path.join(os.path.dirname(__file__), 'platform.db')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS platform_config (
    id  INTEGER PRIMARY KEY CHECK (id = 1),
    kid TEXT NOT NULL,
    private_key_pem TEXT NOT NULL,
    public_key_pem  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lti_tools (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    client_id     TEXT UNIQUE NOT NULL,
    deployment_id TEXT UNIQUE NOT NULL,
    login_url     TEXT NOT NULL,
    redirect_uri  TEXT NOT NULL,
    jwks_url      TEXT NOT NULL,
    target_link_uri TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS courses (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    teacher_id INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS activities (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER NOT NULL,
    tool_id   INTEGER NOT NULL,
    name TEXT NOT NULL,
    resource_link_id TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS lineitems (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER UNIQUE NOT NULL,
    label       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    activity_id INTEGER NOT NULL,
    course_id   INTEGER NOT NULL,
    score       REAL,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, activity_id)
);
CREATE TABLE IF NOT EXISTS access_tokens (
    token      TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
'''


# ── DB ────────────────────────────────────────────────────────────────────────

def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_=None):
    db = g.pop('db', None)
    if db:
        db.close()


def init_db():
    with app.app_context():
        db = get_db()
        db.executescript(SCHEMA)
        if not db.execute('SELECT 1 FROM platform_config').fetchone():
            priv, pub, kid = generate_key_pair()
            db.execute(
                'INSERT INTO platform_config (id, kid, private_key_pem, public_key_pem) '
                'VALUES (1, ?, ?, ?)', [kid, priv, pub]
            )
        db.commit()


# ── Auth ──────────────────────────────────────────────────────────────────────

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = get_db().execute(
            'SELECT * FROM users WHERE username=? AND password_hash=?',
            [request.form['username'], hash_pw(request.form['password'])]
        ).fetchone()
        if user:
            session.permanent = True
            session.update({'user_id': user['id'], 'username': user['username']})
            return redirect(url_for('index'))
        flash('Invalid credentials', 'danger')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            db = get_db()
            db.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)',
                       [request.form['username'], hash_pw(request.form['password'])])
            db.commit()
            flash('Registered! Please login.', 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash('Username already taken', 'danger')
    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def index():
    courses = get_db().execute(
        'SELECT * FROM courses WHERE teacher_id=?', [session['user_id']]
    ).fetchall()
    return render_template('dashboard.html', courses=courses, username=session['username'])


# ── Tool management ───────────────────────────────────────────────────────────

@app.route('/tools')
@login_required
def tools():
    db = get_db()
    tools_list = db.execute('SELECT * FROM lti_tools').fetchall()
    iss = request.host_url.rstrip('/')
    return render_template('tools.html', tools=tools_list, iss=iss)


@app.route('/tools/add', methods=['GET', 'POST'])
@login_required
def add_tool():
    if request.method == 'POST':
        client_id     = 'client_' + uuid.uuid4().hex[:12]
        deployment_id = 'dep_'    + uuid.uuid4().hex[:12]
        db = get_db()
        db.execute(
            'INSERT INTO lti_tools '
            '(name, client_id, deployment_id, login_url, redirect_uri, jwks_url, target_link_uri) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            [request.form['name'], client_id, deployment_id,
             request.form['login_url'], request.form['redirect_uri'],
             request.form['jwks_url'], request.form['target_link_uri']]
        )
        db.commit()
        flash(f'Tool registered. client_id: {client_id}', 'success')
        return redirect(url_for('tools'))
    return render_template('add_tool.html')


# ── Courses ───────────────────────────────────────────────────────────────────

@app.route('/courses/add', methods=['POST'])
@login_required
def add_course():
    db = get_db()
    db.execute('INSERT INTO courses (name, teacher_id) VALUES (?, ?)',
               [request.form['name'], session['user_id']])
    db.commit()
    return redirect(url_for('index'))


@app.route('/courses/<int:course_id>')
@login_required
def course_detail(course_id):
    db = get_db()
    course     = db.execute('SELECT * FROM courses WHERE id=?', [course_id]).fetchone()
    activities = db.execute(
        'SELECT a.*, t.name as tool_name FROM activities a '
        'JOIN lti_tools t ON a.tool_id=t.id WHERE a.course_id=?', [course_id]
    ).fetchall()
    tools_list = db.execute('SELECT * FROM lti_tools').fetchall()
    grades     = db.execute(
        'SELECT g.*, u.username FROM grades g '
        'JOIN users u ON g.user_id=u.id '
        'WHERE g.course_id=? AND g.score IS NOT NULL ORDER BY g.updated_at DESC',
        [course_id]
    ).fetchall()
    return render_template('course_detail.html', course=course, activities=activities,
                           tools=tools_list, grades=grades)


@app.route('/courses/<int:course_id>/activities/add', methods=['POST'])
@login_required
def add_activity(course_id):
    db = get_db()
    db.execute(
        'INSERT INTO activities (course_id, tool_id, name, resource_link_id) VALUES (?, ?, ?, ?)',
        [course_id, request.form['tool_id'], request.form['name'], 'rl_' + uuid.uuid4().hex]
    )
    db.commit()
    return redirect(url_for('course_detail', course_id=course_id))


# ── LTI 1.3 Launch: Step 1 — redirect to tool's login initiation URL ──────────

@app.route('/lti/launch/<int:activity_id>')
@login_required
def lti_launch(activity_id):
    db  = get_db()
    row = db.execute(
        'SELECT a.*, t.login_url, t.client_id, t.target_link_uri '
        'FROM activities a JOIN lti_tools t ON a.tool_id=t.id WHERE a.id=?',
        [activity_id]
    ).fetchone()
    if not row:
        return 'Activity not found', 404

    iss = request.host_url.rstrip('/')
    params = urllib.parse.urlencode({
        'iss':               iss,
        'login_hint':        str(session['user_id']),
        'lti_message_hint':  str(activity_id),
        'target_link_uri':   row['target_link_uri'],
        'client_id':         row['client_id'],
    })
    return redirect(row['login_url'] + '?' + params)


# ── LTI 1.3 Launch: Step 3 — OIDC authorization endpoint ─────────────────────

@app.route('/lti/oidc/auth')
@login_required
def oidc_auth():
    client_id        = request.args.get('client_id', '')
    redirect_uri     = request.args.get('redirect_uri', '')
    login_hint       = request.args.get('login_hint', '')       # user_id
    lti_message_hint = request.args.get('lti_message_hint', '') # activity_id
    state            = request.args.get('state', '')
    nonce            = request.args.get('nonce', '')

    db   = get_db()
    tool = db.execute('SELECT * FROM lti_tools WHERE client_id=?', [client_id]).fetchone()
    if not tool:
        return 'Unknown client_id', 400
    if tool['redirect_uri'] != redirect_uri:
        return 'redirect_uri mismatch', 400

    activity_id = int(lti_message_hint)
    activity    = db.execute('SELECT * FROM activities WHERE id=?', [activity_id]).fetchone()

    # Ensure lineitem exists
    li = db.execute('SELECT * FROM lineitems WHERE activity_id=?', [activity_id]).fetchone()
    if not li:
        db.execute('INSERT INTO lineitems (activity_id, label) VALUES (?, ?)',
                   [activity_id, activity['name']])
        db.commit()
        li = db.execute('SELECT * FROM lineitems WHERE activity_id=?', [activity_id]).fetchone()

    # Ensure grade row exists for this user+activity
    db.execute(
        'INSERT OR IGNORE INTO grades (user_id, activity_id, course_id) VALUES (?, ?, ?)',
        [session['user_id'], activity_id, activity['course_id']]
    )
    db.commit()

    iss          = request.host_url.rstrip('/')
    lineitem_url = iss + f'/lti/ags/lineitems/{li["id"]}'
    return_url   = iss + f'/courses/{activity["course_id"]}'
    config       = db.execute('SELECT * FROM platform_config WHERE id=1').fetchone()

    id_token = make_id_token(
        private_pem=config['private_key_pem'],
        kid=config['kid'],
        iss=iss,
        aud=client_id,
        sub=login_hint,               # user_id as string
        nonce=nonce,
        deployment_id=tool['deployment_id'],
        resource_link_id=activity['resource_link_id'],
        resource_link_title=activity['name'],
        context_id=str(activity['course_id']),
        user_name=session['username'],
        target_link_uri=tool['target_link_uri'],
        lineitem_url=lineitem_url,
        return_url=return_url,
    )
    return render_template('oidc_response.html',
                           redirect_uri=redirect_uri,
                           id_token=id_token,
                           state=state)


# ── LTI 1.3 JWKS ─────────────────────────────────────────────────────────────

@app.route('/lti/jwks')
def lti_jwks():
    config = get_db().execute('SELECT * FROM platform_config WHERE id=1').fetchone()
    return jsonify({'keys': [public_key_to_jwk(config['public_key_pem'], config['kid'])]})


# ── LTI 1.3 Token endpoint (JWT Bearer → access token for AGS) ───────────────

@app.route('/lti/token', methods=['POST'])
def lti_token():
    if request.form.get('grant_type') != 'client_credentials':
        return jsonify({'error': 'unsupported_grant_type'}), 400

    assertion = request.form.get('client_assertion', '')
    try:
        # Peek at client_id without verifying yet
        unverified = jwt.decode(assertion, options={'verify_signature': False})
        client_id  = unverified.get('iss') or unverified.get('sub', '')
    except Exception:
        return jsonify({'error': 'invalid_client'}), 400

    db   = get_db()
    tool = db.execute('SELECT * FROM lti_tools WHERE client_id=?', [client_id]).fetchone()
    if not tool:
        return jsonify({'error': 'invalid_client'}), 400

    token_url = request.host_url.rstrip('/') + '/lti/token'
    try:
        verify_tool_jwt(assertion, tool['jwks_url'], token_url)
    except Exception as e:
        return jsonify({'error': 'invalid_client', 'error_description': str(e)}), 400

    token      = uuid.uuid4().hex
    expires_at = int(time.time()) + 3600
    db.execute('INSERT INTO access_tokens (token, client_id, expires_at) VALUES (?, ?, ?)',
               [token, client_id, expires_at])
    db.commit()
    return jsonify({'access_token': token, 'token_type': 'Bearer',
                    'expires_in': 3600, 'scope': request.form.get('scope', '')})


# ── AGS: score submission ─────────────────────────────────────────────────────

@app.route('/lti/ags/lineitems/<int:lineitem_id>/scores', methods=['POST'])
def ags_scores(lineitem_id):
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return 'Unauthorized', 401

    db        = get_db()
    token_row = db.execute(
        'SELECT 1 FROM access_tokens WHERE token=? AND expires_at>?',
        [auth[7:], int(time.time())]
    ).fetchone()
    if not token_row:
        return 'Unauthorized', 401

    li = db.execute('SELECT * FROM lineitems WHERE id=?', [lineitem_id]).fetchone()
    if not li:
        return 'Not Found', 404

    data          = request.get_json(force=True)
    user_id       = int(data['userId'])
    score_given   = float(data.get('scoreGiven', 0))
    score_maximum = float(data.get('scoreMaximum', 100))
    score         = score_given / score_maximum if score_maximum else 0.0

    db.execute(
        'UPDATE grades SET score=?, updated_at=CURRENT_TIMESTAMP '
        'WHERE user_id=? AND activity_id=?',
        [score, user_id, li['activity_id']]
    )
    db.commit()
    return '', 204


# ── OIDC Discovery (optional, handy for tool auto-config) ────────────────────

@app.route('/.well-known/openid-configuration')
def oidc_config():
    base = request.host_url.rstrip('/')
    return jsonify({
        'issuer':                 base,
        'jwks_uri':               base + '/lti/jwks',
        'authorization_endpoint': base + '/lti/oidc/auth',
        'token_endpoint':         base + '/lti/token',
    })


import jwt  # noqa – needed for unverified decode in token endpoint


if __name__ == '__main__':
    init_db()
    app.run(port=8001, debug=True)
