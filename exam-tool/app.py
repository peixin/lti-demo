import json, os, sqlite3, urllib.parse, uuid

from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

from lti import (generate_key_pair, get_access_token, post_score,
                 public_key_to_jwk, verify_id_token)

app = Flask(__name__)
app.secret_key = 'exam-tool-demo-secret-change-in-prod'
app.config['SESSION_COOKIE_NAME'] = 'examtool_session'

ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin')
DATABASE = os.path.join(os.path.dirname(__file__), 'exam-tool.db')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS tool_config (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    kid              TEXT NOT NULL,
    private_key_pem  TEXT NOT NULL,
    public_key_pem   TEXT NOT NULL,
    platform_iss     TEXT,
    client_id        TEXT,
    deployment_id    TEXT,
    platform_oidc_auth_url TEXT,
    platform_jwks_url      TEXT,
    platform_token_url     TEXT
);
CREATE TABLE IF NOT EXISTS oidc_state (
    state      TEXT PRIMARY KEY,
    nonce      TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS lti_sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT UNIQUE NOT NULL,
    sub              TEXT NOT NULL,
    user_name        TEXT,
    deployment_id    TEXT NOT NULL,
    resource_link_id TEXT NOT NULL,
    context_id       TEXT,
    lineitem_url     TEXT,
    return_url       TEXT,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS questions (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    text    TEXT NOT NULL,
    options TEXT NOT NULL,
    answer  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    answers      TEXT NOT NULL,
    score        REAL NOT NULL,
    submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
'''

SAMPLE_QUESTIONS = [
    ('Python 中哪个语法用于创建空列表？',           ['()', '[]', '{}', '<>'],           1),
    ('HTTP 状态码 404 表示什么？',                  ['服务器内部错误', '请求成功', '资源未找到', '未授权'], 2),
    ('LTI 全称是什么？',                           ['Learning Tool Interface', 'Learning Tools Interoperability',
                                                   'Linked Teaching Interface', 'Learning Technology Integration'], 1),
    ('LTI 1.3 使用什么认证机制？',                  ['OAuth 1.0a HMAC-SHA1', 'Basic Auth', 'OIDC + JWT RS256', 'API Key'], 2),
    ('LTI 1.3 成绩回传服务叫什么？',               ['Basic Outcomes', 'Grade Passback', 'AGS (Assignment and Grade Services)', 'Score API'], 2),
]


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
        if not db.execute('SELECT 1 FROM tool_config').fetchone():
            priv, pub, kid = generate_key_pair()
            db.execute(
                'INSERT INTO tool_config (id, kid, private_key_pem, public_key_pem) '
                'VALUES (1, ?, ?, ?)', [kid, priv, pub]
            )
        if not db.execute('SELECT 1 FROM questions').fetchone():
            for text, opts, ans in SAMPLE_QUESTIONS:
                db.execute(
                    'INSERT INTO questions (text, options, answer) VALUES (?, ?, ?)',
                    [text, json.dumps(opts, ensure_ascii=False), ans]
                )
        db.commit()


# ── Admin ─────────────────────────────────────────────────────────────────────

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['admin'] = True
            return redirect(url_for('admin'))
        flash('Wrong password', 'danger')
    return render_template('admin_login.html')


@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_login'))


@app.route('/admin', methods=['GET', 'POST'])
def admin():
    if not session.get('admin'):
        return redirect(url_for('admin_login'))

    db     = get_db()
    config = db.execute('SELECT * FROM tool_config WHERE id=1').fetchone()

    if request.method == 'POST':
        db.execute(
            'UPDATE tool_config SET platform_iss=?, client_id=?, deployment_id=?, '
            'platform_oidc_auth_url=?, platform_jwks_url=?, platform_token_url=? '
            'WHERE id=1',
            [request.form['platform_iss'], request.form['client_id'],
             request.form['deployment_id'], request.form['platform_oidc_auth_url'],
             request.form['platform_jwks_url'], request.form['platform_token_url']]
        )
        db.commit()
        flash('Platform config saved', 'success')
        config = db.execute('SELECT * FROM tool_config WHERE id=1').fetchone()

    raw_attempts = db.execute(
        'SELECT a.id, a.score, a.answers, a.submitted_at, '
        '       s.user_name, s.resource_link_id '
        'FROM attempts a JOIN lti_sessions s ON a.session_id=s.session_id '
        'ORDER BY a.submitted_at DESC'
    ).fetchall()
    raw_qs = db.execute('SELECT id, text, answer, options FROM questions').fetchall()
    questions = [{'id': q['id'], 'text': q['text'], 'answer': q['answer'],
                  'options': json.loads(q['options'])} for q in raw_qs]
    attempts = []
    for a in raw_attempts:
        ans    = json.loads(a['answers']) if a['answers'] else {}
        detail = [{'text': q['text'], 'options': q['options'],
                   'correct': q['answer'], 'chosen': ans.get(str(q['id']), -1)}
                  for q in questions]
        attempts.append({**dict(a), 'detail': detail})

    base_url = request.host_url.rstrip('/')
    tool_info = {
        'login_url':      base_url + '/lti/login',
        'redirect_uri':   base_url + '/lti/launch',
        'jwks_url':       base_url + '/lti/jwks',
        'target_link_uri': base_url + '/exam',
    }
    return render_template('admin.html', config=config, attempts=attempts,
                           questions=questions, tool_info=tool_info)


# ── Tool JWKS ─────────────────────────────────────────────────────────────────

@app.route('/lti/jwks')
def lti_jwks():
    config = get_db().execute('SELECT * FROM tool_config WHERE id=1').fetchone()
    return jsonify({'keys': [public_key_to_jwk(config['public_key_pem'], config['kid'])]})


# ── LTI 1.3 Step 2: Login initiation ─────────────────────────────────────────

@app.route('/lti/login', methods=['GET', 'POST'])
def lti_login():
    get = lambda k: request.values.get(k, '')

    state = uuid.uuid4().hex
    nonce = uuid.uuid4().hex
    db    = get_db()
    db.execute('INSERT INTO oidc_state (state, nonce) VALUES (?, ?)', [state, nonce])
    # Clean up old states (> 10 min)
    db.execute("DELETE FROM oidc_state WHERE created_at < datetime('now', '-10 minutes')")
    db.commit()

    config = db.execute('SELECT * FROM tool_config WHERE id=1').fetchone()
    if not config or not config['platform_oidc_auth_url']:
        return render_template('error.html',
            message='Tool not configured. Please set platform config at /admin.')

    redirect_uri = request.host_url.rstrip('/') + '/lti/launch'
    auth_params  = urllib.parse.urlencode({
        'scope':             'openid',
        'response_type':     'id_token',
        'client_id':         config['client_id'],
        'redirect_uri':      redirect_uri,
        'login_hint':        get('login_hint'),
        'lti_message_hint':  get('lti_message_hint'),
        'state':             state,
        'nonce':             nonce,
        'response_mode':     'form_post',
        'prompt':            'none',
    })
    return redirect(config['platform_oidc_auth_url'] + '?' + auth_params)


# ── LTI 1.3 Step 4: OIDC callback — validate id_token ────────────────────────

@app.route('/lti/launch', methods=['POST'])
def lti_launch():
    id_token = request.form.get('id_token', '')
    state    = request.form.get('state', '')

    db     = get_db()
    stored = db.execute('SELECT * FROM oidc_state WHERE state=?', [state]).fetchone()
    if not stored:
        return render_template('error.html', message='Invalid or expired state.'), 400

    db.execute('DELETE FROM oidc_state WHERE state=?', [state])
    db.commit()

    config = db.execute('SELECT * FROM tool_config WHERE id=1').fetchone()
    try:
        claims = verify_id_token(id_token, config['platform_jwks_url'],
                                 config['client_id'], config['platform_iss'])
    except Exception as e:
        return render_template('error.html', message=f'JWT verification failed: {e}'), 403

    if claims.get('nonce') != stored['nonce']:
        return render_template('error.html', message='Nonce mismatch.'), 400

    dep_id = claims.get('https://purl.imsglobal.org/spec/lti/claim/deployment_id', '')
    if dep_id != config['deployment_id']:
        return render_template('error.html', message='Unknown deployment_id.'), 400

    rl     = claims.get('https://purl.imsglobal.org/spec/lti/claim/resource_link', {})
    ctx    = claims.get('https://purl.imsglobal.org/spec/lti/claim/context', {})
    ags    = claims.get('https://purl.imsglobal.org/spec/lti-ags/claim/endpoint', {})
    lp     = claims.get('https://purl.imsglobal.org/spec/lti/claim/launch_presentation', {})
    custom = claims.get('https://purl.imsglobal.org/spec/lti/claim/custom', {})

    if custom.get('view') == 'detail':
        sub              = claims.get('sub')
        resource_link_id = rl.get('id', '')
        lti_sess = db.execute(
            'SELECT * FROM lti_sessions WHERE sub=? AND resource_link_id=?',
            [sub, resource_link_id]
        ).fetchone()
        if not lti_sess:
            return render_template('error.html', message='No submission found for this user.'), 404
        attempt = db.execute(
            'SELECT * FROM attempts WHERE session_id=?', [lti_sess['session_id']]
        ).fetchone()
        if not attempt:
            return render_template('error.html', message='No submission found for this user.'), 404
        rows      = db.execute('SELECT * FROM questions').fetchall()
        answers   = json.loads(attempt['answers'])
        questions = []
        for q in rows:
            opts   = json.loads(q['options'])
            chosen = answers.get(str(q['id']), -1)
            questions.append({
                'text':    q['text'],
                'options': opts,
                'chosen':  chosen,
                'correct': q['answer'],
            })
        return render_template('detail.html',
                               user_name=lti_sess['user_name'],
                               score=attempt['score'],
                               questions=questions,
                               return_url=lp.get('return_url', ''))

    sess_id = uuid.uuid4().hex
    db.execute(
        'INSERT INTO lti_sessions '
        '(session_id, sub, user_name, deployment_id, resource_link_id, '
        ' context_id, lineitem_url, return_url) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        [sess_id, claims.get('sub'), claims.get('name', 'Learner'),
         dep_id, rl.get('id', ''), ctx.get('id', ''),
         ags.get('lineitem', ''), lp.get('return_url', '')]
    )
    db.commit()
    session['lti_session_id'] = sess_id
    return redirect(url_for('exam'))


# ── Exam ──────────────────────────────────────────────────────────────────────

@app.route('/exam')
def exam():
    sess_id = session.get('lti_session_id')
    if not sess_id:
        return render_template('error.html',
            message='No active LTI session. Please launch from the platform.')
    db       = get_db()
    lti_sess = db.execute('SELECT * FROM lti_sessions WHERE session_id=?', [sess_id]).fetchone()
    if not lti_sess:
        return render_template('error.html', message='Session expired.')
    if db.execute('SELECT 1 FROM attempts WHERE session_id=?', [sess_id]).fetchone():
        return redirect(url_for('result'))
    rows      = db.execute('SELECT id, text, options FROM questions').fetchall()
    questions = [{'id': r['id'], 'text': r['text'], 'options': json.loads(r['options'])}
                 for r in rows]
    return render_template('exam.html', user_name=lti_sess['user_name'], questions=questions)


@app.route('/exam/submit', methods=['POST'])
def submit_exam():
    sess_id = session.get('lti_session_id')
    if not sess_id:
        return render_template('error.html', message='No session.')
    db        = get_db()
    lti_sess  = db.execute('SELECT * FROM lti_sessions WHERE session_id=?', [sess_id]).fetchone()
    questions = db.execute('SELECT * FROM questions').fetchall()

    answers, correct = {}, 0
    for q in questions:
        raw    = request.form.get(f'q_{q["id"]}', '')
        chosen = int(raw) if raw.isdigit() else -1
        answers[str(q['id'])] = chosen
        if chosen == q['answer']:
            correct += 1
    score = correct / len(questions) if questions else 0.0

    db.execute('INSERT INTO attempts (session_id, answers, score) VALUES (?, ?, ?)',
               [sess_id, json.dumps(answers), score])
    db.commit()

    # AGS score passback
    if lti_sess['lineitem_url']:
        config = db.execute('SELECT * FROM tool_config WHERE id=1').fetchone()
        try:
            token = get_access_token(config['platform_token_url'],
                                     config['private_key_pem'],
                                     config['kid'], config['client_id'])
            post_score(token, lti_sess['lineitem_url'], lti_sess['sub'], score)
        except Exception:
            pass

    return redirect(url_for('result'))


@app.route('/result')
def result():
    sess_id = session.get('lti_session_id')
    if not sess_id:
        return render_template('error.html', message='No session.')
    db       = get_db()
    attempt  = db.execute('SELECT * FROM attempts WHERE session_id=?', [sess_id]).fetchone()
    lti_sess = db.execute('SELECT * FROM lti_sessions WHERE session_id=?', [sess_id]).fetchone()
    if not attempt:
        return redirect(url_for('exam'))
    return render_template('result.html', score=attempt['score'],
                           user_name=lti_sess['user_name'],
                           return_url=lti_sess['return_url'])


if __name__ == '__main__':
    init_db()
    app.run(port=8002, debug=True)
