from datetime import timedelta
from functools import wraps
import hashlib, json, os, sqlite3, time, urllib.parse, uuid

from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

from lti import (LTI, LTI_AGS, LTI_DL, ROLE_INSTRUCTOR, ROLE_LEARNER,
                 generate_key_pair, make_id_token, public_key_to_jwk,
                 verify_dl_response, verify_tool_jwt)

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
    resource_link_id TEXT UNIQUE NOT NULL,
    deep_link_uri    TEXT,
    deep_link_custom TEXT
);
CREATE TABLE IF NOT EXISTS lineitems (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id   INTEGER NOT NULL,
    label         TEXT NOT NULL,
    tag           TEXT,
    score_maximum REAL DEFAULT 100
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
CREATE TABLE IF NOT EXISTS score_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    lineitem_id       INTEGER NOT NULL,
    user_id           INTEGER NOT NULL,
    score_given       REAL,
    score_maximum     REAL,
    activity_progress TEXT NOT NULL,
    grading_progress  TEXT NOT NULL,
    timestamp         TEXT NOT NULL,
    tool_event_id     TEXT NOT NULL,
    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(lineitem_id, user_id, tool_event_id)
);
CREATE TABLE IF NOT EXISTS access_tokens (
    token      TEXT PRIMARY KEY,
    client_id  TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
'''


# ── i18n Translations ─────────────────────────────────────────────────────────

TRANSLATIONS = {}
LOCALES_DIR = os.path.join(os.path.dirname(__file__), 'locales')
try:
    for filename in os.listdir(LOCALES_DIR):
        if filename.endswith('.json'):
            lang_code = filename[:-5]  # e.g., 'zh-CN'
            filepath = os.path.join(LOCALES_DIR, filename)
            with open(filepath, 'r', encoding='utf-8') as f:
                TRANSLATIONS[lang_code] = json.load(f)
except Exception as e:
    print(f"Error loading translation files: {e}")

def get_lang():
    lang = request.args.get('lang')
    if lang in TRANSLATIONS:
        session['lang'] = lang
        return lang
    if 'lang' in session:
        return session['lang']
    al = request.headers.get('Accept-Language', '')
    if al:
        best = request.accept_languages.best_match(['ko', 'zh-CN', 'zh-TW', 'zh-HK', 'en'])
        if best:
            if best in ['zh-HK', 'zh-TW']:
                return 'zh-TW'
            if best == 'zh-CN' or best == 'zh':
                return 'zh-CN'
            return best
    return 'zh-CN'

def py_t(key, **kwargs):
    lang = get_lang()
    lang_dict = TRANSLATIONS.get(lang, TRANSLATIONS.get('zh-CN', {}))
    val = lang_dict.get(key, TRANSLATIONS.get('zh-CN', {}).get(key, key))
    try:
        return val.format(**kwargs)
    except Exception:
        return val

@app.route('/set-lang/<lang>')
def set_lang(lang):
    if lang in TRANSLATIONS:
        session['lang'] = lang
    next_page = request.referrer or url_for('index')
    return redirect(next_page)

@app.context_processor
def inject_translations():
    lang = get_lang()
    def t(key, **kwargs):
        return py_t(key, **kwargs)
    return dict(t=t, current_lang=lang, TRANSLATIONS=TRANSLATIONS)


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


def migrate_db():
    """Add columns the existing demo DB may be missing."""
    with app.app_context():
        db = get_db()
        # lineitems: add tag / score_maximum
        cols = {row[1] for row in db.execute('PRAGMA table_info(lineitems)').fetchall()}
        if 'tag' not in cols:
            db.executescript('''
                CREATE TABLE lineitems_v2 (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    activity_id   INTEGER NOT NULL,
                    label         TEXT NOT NULL,
                    tag           TEXT,
                    score_maximum REAL DEFAULT 100
                );
                INSERT INTO lineitems_v2 (id, activity_id, label)
                    SELECT id, activity_id, label FROM lineitems;
                DROP TABLE lineitems;
                ALTER TABLE lineitems_v2 RENAME TO lineitems;
            ''')
            db.commit()
        # activities: add deep_link_uri / deep_link_custom
        acols = {row[1] for row in db.execute('PRAGMA table_info(activities)').fetchall()}
        if 'deep_link_uri' not in acols:
            db.execute('ALTER TABLE activities ADD COLUMN deep_link_uri TEXT')
        if 'deep_link_custom' not in acols:
            db.execute('ALTER TABLE activities ADD COLUMN deep_link_custom TEXT')
        db.commit()
        # score_events: add tool_event_id (NOT NULL UNIQUE per activity+user).
        # SQLite can't ADD NOT NULL columns directly, so rebuild the table.
        evcols = {row[1] for row in db.execute('PRAGMA table_info(score_events)').fetchall()}
        if 'tool_event_id' not in evcols:
            db.executescript('''
                CREATE TABLE score_events_v2 (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    lineitem_id       INTEGER NOT NULL,
                    user_id           INTEGER NOT NULL,
                    score_given       REAL,
                    score_maximum     REAL,
                    activity_progress TEXT NOT NULL,
                    grading_progress  TEXT NOT NULL,
                    timestamp         TEXT NOT NULL,
                    tool_event_id     TEXT NOT NULL,
                    created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(lineitem_id, user_id, tool_event_id)
                );
                INSERT INTO score_events_v2
                    (id, lineitem_id, user_id, score_given, score_maximum,
                     activity_progress, grading_progress, timestamp, tool_event_id, created_at)
                SELECT id, lineitem_id, user_id, score_given, score_maximum,
                       activity_progress, grading_progress, timestamp,
                       timestamp AS tool_event_id, created_at
                FROM score_events;
                DROP TABLE score_events;
                ALTER TABLE score_events_v2 RENAME TO score_events;
            ''')
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
        flash(py_t('invalid_credentials'), 'danger')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        try:
            db = get_db()
            db.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)',
                       [request.form['username'], hash_pw(request.form['password'])])
            db.commit()
            flash(py_t('registered_success'), 'success')
            return redirect(url_for('login'))
        except sqlite3.IntegrityError:
            flash(py_t('username_taken'), 'danger')
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
            [request.form['name'].strip(), client_id, deployment_id,
             request.form['login_url'].strip(), request.form['redirect_uri'].strip(),
             request.form['jwks_url'].strip(), request.form['target_link_uri'].strip()]
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
    # Decorate activities with parsed deep_link_custom for display
    activities = [
        {**dict(a),
         'dl_custom': json.loads(a['deep_link_custom']) if a['deep_link_custom'] else None}
        for a in activities
    ]
    tools_list = db.execute('SELECT * FROM lti_tools').fetchall()
    grades     = db.execute(
        'SELECT g.*, u.username FROM grades g '
        'JOIN users u ON g.user_id=u.id '
        'WHERE g.course_id=? AND g.score IS NOT NULL ORDER BY g.updated_at DESC',
        [course_id]
    ).fetchall()
    dl_configured = request.args.get('dl_configured', '').strip()
    dl_done_act = None
    if dl_configured.isdigit():
        dl_done_act = next((a for a in activities if a['id'] == int(dl_configured)), None)
    return render_template('course_detail.html', course=course, activities=activities,
                           tools=tools_list, grades=grades,
                           dl_done_act=dl_done_act)


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


# ── LTI 1.3 Launch starters ──────────────────────────────────────────────────
#
# All three launch flows funnel into the tool's login_initiation_url with a
# different `lti_message_hint`:
#   - 'launch:<aid>'          → LtiResourceLinkRequest   (student / teacher preview)
#   - 'dl:<aid>'              → LtiDeepLinkingRequest    (teacher configures content)
#   - 'review:<aid>:<uid>[:<event_id>]' → LtiSubmissionReviewRequest

def _start_oidc(activity_id, message_hint):
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
        'iss':              iss,
        'login_hint':       str(session['user_id']),
        'lti_message_hint': message_hint,
        'target_link_uri':  row['deep_link_uri'] or row['target_link_uri'],
        'client_id':        row['client_id'],
    })
    return redirect(row['login_url'] + '?' + params)


@app.route('/lti/launch/<int:activity_id>')
@login_required
def lti_launch(activity_id):
    return _start_oidc(activity_id, f'launch:{activity_id}')


@app.route('/lti/dl/<int:activity_id>')
@login_required
def lti_dl(activity_id):
    return _start_oidc(activity_id, f'dl:{activity_id}')


@app.route('/lti/launch_review/<int:activity_id>/<int:target_user_id>')
@login_required
def lti_launch_review(activity_id, target_user_id):
    event_id = request.args.get('event_id', '').strip()
    hint = f'review:{activity_id}:{target_user_id}'
    if event_id:
        hint += f':{event_id}'
    return _start_oidc(activity_id, hint)


# ── LTI 1.3 OIDC authorization endpoint — sign id_token per message type ─────

@app.route('/lti/oidc/auth')
@login_required
def oidc_auth():
    client_id    = request.args.get('client_id', '')
    redirect_uri = request.args.get('redirect_uri', '').strip()
    login_hint   = request.args.get('login_hint', '')
    hint         = request.args.get('lti_message_hint', '')
    state        = request.args.get('state', '')
    nonce        = request.args.get('nonce', '')

    db   = get_db()
    tool = db.execute('SELECT * FROM lti_tools WHERE client_id=?', [client_id]).fetchone()
    if not tool:
        return 'Unknown client_id', 400
    if tool['redirect_uri'] != redirect_uri:
        return 'redirect_uri mismatch', 400

    config = db.execute('SELECT * FROM platform_config WHERE id=1').fetchone()
    iss    = request.host_url.rstrip('/')

    if hint.startswith('dl:'):
        return _sign_deep_linking(hint, client_id, tool, config, iss, login_hint,
                                  redirect_uri, state, nonce)
    if hint.startswith('review:'):
        return _sign_submission_review(hint, client_id, tool, config, iss, login_hint,
                                       redirect_uri, state, nonce)
    # Default: LtiResourceLinkRequest. Accept both 'launch:<aid>' and legacy bare '<aid>'.
    if hint.startswith('launch:'):
        activity_id = int(hint.split(':', 1)[1])
    else:
        try:
            activity_id = int(hint)
        except ValueError:
            return 'Invalid lti_message_hint', 400
    return _sign_resource_link(activity_id, client_id, tool, config, iss, login_hint,
                               redirect_uri, state, nonce)


def _sign_resource_link(activity_id, client_id, tool, config, iss, login_hint,
                        redirect_uri, state, nonce):
    db       = get_db()
    activity = db.execute('SELECT * FROM activities WHERE id=?', [activity_id]).fetchone()
    if not activity:
        return 'Activity not found', 404

    # Ensure default lineitem exists for this activity
    li = db.execute(
        'SELECT * FROM lineitems WHERE activity_id=? AND tag IS NULL', [activity_id]
    ).fetchone()
    if not li:
        db.execute(
            'INSERT INTO lineitems (activity_id, label, score_maximum) VALUES (?, ?, ?)',
            [activity_id, activity['name'], 100]
        )
        db.commit()
        li = db.execute(
            'SELECT * FROM lineitems WHERE activity_id=? AND tag IS NULL', [activity_id]
        ).fetchone()

    # Ensure a gradebook row exists so AGS can update it later
    db.execute(
        'INSERT OR IGNORE INTO grades (user_id, activity_id, course_id) VALUES (?, ?, ?)',
        [session['user_id'], activity_id, activity['course_id']]
    )
    db.commit()

    lineitem_url = iss + f'/lti/ags/lineitems/{li["id"]}'
    return_url   = iss + f'/courses/{activity["course_id"]}'
    nrps_url     = iss + f'/lti/nrps/{activity["course_id"]}/memberships'
    # If the activity was DL-configured, prefer the DL'd target URL + custom.
    target_link_uri = activity['deep_link_uri'] or tool['target_link_uri']
    custom = (json.loads(activity['deep_link_custom'])
              if activity['deep_link_custom'] else None)

    id_token = make_id_token(
        private_pem=config['private_key_pem'],
        kid=config['kid'],
        iss=iss,
        aud=client_id,
        sub=login_hint,
        nonce=nonce,
        deployment_id=tool['deployment_id'],
        message_type='LtiResourceLinkRequest',
        resource_link={'id': activity['resource_link_id'], 'title': activity['name']},
        context={'id': str(activity['course_id']),
                 'type': ['http://purl.imsglobal.org/vocab/lis/v2/course#CourseOffering']},
        user_name=session['username'],
        target_link_uri=target_link_uri,
        lineitem_url=lineitem_url,
        return_url=return_url,
        nrps_url=nrps_url,
        custom=custom,
        roles=[ROLE_LEARNER],
    )
    return render_template('oidc_response.html',
                           redirect_uri=redirect_uri,
                           id_token=id_token,
                           state=state)


def _sign_deep_linking(hint, client_id, tool, config, iss, login_hint,
                      redirect_uri, state, nonce):
    db          = get_db()
    activity_id = int(hint.split(':', 1)[1])
    activity    = db.execute('SELECT * FROM activities WHERE id=?', [activity_id]).fetchone()
    if not activity:
        return 'Activity not found', 404

    dl_settings = {
        'deep_link_return_url': iss + '/lti/dl/response',
        'accept_types': ['ltiResourceLink'],
        'accept_presentation_document_targets': ['iframe', 'window'],
        'accept_multiple': False,
        'auto_create':     False,
        'data':            f'activity_id={activity_id}',
    }
    id_token = make_id_token(
        private_pem=config['private_key_pem'],
        kid=config['kid'],
        iss=iss,
        aud=client_id,
        sub=login_hint,
        nonce=nonce,
        deployment_id=tool['deployment_id'],
        message_type='LtiDeepLinkingRequest',
        context={'id': str(activity['course_id']),
                 'type': ['http://purl.imsglobal.org/vocab/lis/v2/course#CourseOffering']},
        user_name=session['username'],
        target_link_uri=tool['target_link_uri'],
        return_url=iss + f'/courses/{activity["course_id"]}',
        roles=[ROLE_INSTRUCTOR],
        deep_linking_settings=dl_settings,
    )
    return render_template('oidc_response.html',
                           redirect_uri=redirect_uri,
                           id_token=id_token,
                           state=state)


def _sign_submission_review(hint, client_id, tool, config, iss, login_hint,
                            redirect_uri, state, nonce):
    db    = get_db()
    parts = hint.split(':')
    activity_id    = int(parts[1])
    target_user_id = parts[2]
    event_id       = parts[3] if len(parts) > 3 else None

    activity = db.execute('SELECT * FROM activities WHERE id=?', [activity_id]).fetchone()
    if not activity:
        return 'Activity not found', 404
    target = db.execute('SELECT * FROM users WHERE id=?', [target_user_id]).fetchone()
    if not target:
        return 'Target user not found', 404
    li = db.execute(
        'SELECT * FROM lineitems WHERE activity_id=? AND tag IS NULL', [activity_id]
    ).fetchone()
    lineitem_url = iss + f'/lti/ags/lineitems/{li["id"]}' if li else None

    target_link_uri = activity['deep_link_uri'] or tool['target_link_uri']
    custom = json.loads(activity['deep_link_custom']) if activity['deep_link_custom'] else {}
    if event_id:
        # Look up the tool_event_id stored when the Tool posted this score event.
        # This is the Tool's own submissionId (or timestamp fallback), NOT the LMS
        # auto-increment primary key — so the Tool can directly locate its attempt row.
        li_for_event = db.execute(
            'SELECT * FROM lineitems WHERE activity_id=? AND tag IS NULL', [activity_id]
        ).fetchone()
        if li_for_event:
            ev = db.execute(
                'SELECT tool_event_id FROM score_events WHERE id=? AND lineitem_id=?',
                [event_id, li_for_event['id']]
            ).fetchone()
            if ev:
                custom['tool_event_id'] = ev['tool_event_id']

    id_token = make_id_token(
        private_pem=config['private_key_pem'],
        kid=config['kid'],
        iss=iss,
        aud=client_id,
        sub=login_hint,                    # operator (teacher)
        nonce=nonce,
        deployment_id=tool['deployment_id'],
        message_type='LtiSubmissionReviewRequest',
        resource_link={'id': activity['resource_link_id'], 'title': activity['name']},
        context={'id': str(activity['course_id']),
                 'type': ['http://purl.imsglobal.org/vocab/lis/v2/course#CourseOffering']},
        user_name=session['username'],
        target_link_uri=target_link_uri,
        return_url=iss + f'/courses/{activity["course_id"]}',
        roles=[ROLE_INSTRUCTOR],
        for_user={'user_id': str(target_user_id),
                  'name':     target['username']},
        custom=custom or None,
        lineitem_url=lineitem_url,
    )
    return render_template('oidc_response.html',
                           redirect_uri=redirect_uri,
                           id_token=id_token,
                           state=state)


# ── DeepLinkingResponse handler — receives Tool's JWT ────────────────────────

@app.route('/lti/dl/response', methods=['POST'])
def lti_dl_response():
    token = request.form.get('JWT', '')
    try:
        unverified = jwt.decode(token, options={'verify_signature': False})
    except Exception as e:
        return f'Invalid JWT: {e}', 400

    client_id = unverified.get('iss', '')
    db   = get_db()
    tool = db.execute('SELECT * FROM lti_tools WHERE client_id=?', [client_id]).fetchone()
    if not tool:
        return 'Unknown client_id', 400

    iss = request.host_url.rstrip('/')
    try:
        claims = verify_dl_response(token, tool['jwks_url'],
                                    expected_aud=iss, expected_iss=client_id)
    except Exception as e:
        return f'JWT verification failed: {e}', 403

    if claims.get(f'{LTI}/message_type') != 'LtiDeepLinkingResponse':
        return 'Wrong message_type', 400
    if claims.get(f'{LTI}/deployment_id') != tool['deployment_id']:
        return 'deployment_id mismatch', 400

    data = claims.get(f'{LTI_DL}/data', '')
    if not data.startswith('activity_id='):
        return 'Invalid data field', 400
    try:
        activity_id = int(data[len('activity_id='):])
    except ValueError:
        return 'Invalid activity_id in data', 400

    activity = db.execute('SELECT * FROM activities WHERE id=?', [activity_id]).fetchone()
    if not activity:
        return 'Activity not found', 400

    items = claims.get(f'{LTI_DL}/content_items', []) or []
    item  = next((i for i in items if i.get('type') == 'ltiResourceLink'), None)
    if not item or not item.get('url'):
        return 'No valid ltiResourceLink in content_items', 400

    custom = item.get('custom') or {}
    db.execute(
        'UPDATE activities SET deep_link_uri=?, deep_link_custom=? WHERE id=?',
        [item['url'], json.dumps(custom), activity_id]
    )
    db.commit()

    # IMPORTANT: do NOT touch `session` (e.g. via flash()) here. This POST comes
    # cross-site from the Tool, so SameSite=Lax suppresses the teacher's
    # platform session cookie. Writing to session would create a new empty one
    # and clobber the teacher's login on the Set-Cookie reply.
    return redirect(url_for('course_detail',
                            course_id=activity['course_id'],
                            dl_configured=str(activity_id)))


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


# ── AGS: lineitem collection (list + create) ──────────────────────────────────

def _ags_bearer_auth(db):
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return False
    return bool(db.execute(
        'SELECT 1 FROM access_tokens WHERE token=? AND expires_at>?',
        [auth[7:], int(time.time())]
    ).fetchone())


@app.route('/lti/ags/lineitems/<int:lineitem_id>', methods=['GET', 'POST'])
def ags_lineitems(lineitem_id):
    db = get_db()
    if not _ags_bearer_auth(db):
        return 'Unauthorized', 401

    parent = db.execute('SELECT * FROM lineitems WHERE id=?', [lineitem_id]).fetchone()
    if not parent:
        return 'Not Found', 404

    base = request.host_url.rstrip('/')

    if request.method == 'GET':
        rows = db.execute(
            'SELECT * FROM lineitems WHERE activity_id=? AND tag IS NOT NULL',
            [parent['activity_id']]
        ).fetchall()
        return jsonify([{
            'id':           base + f'/lti/ags/lineitems/{r["id"]}',
            'label':        r['label'],
            'tag':          r['tag'],
            'scoreMaximum': r['score_maximum'],
        } for r in rows])

    data   = request.get_json(force=True)
    new_id = db.execute(
        'INSERT INTO lineitems (activity_id, label, tag, score_maximum) VALUES (?, ?, ?, ?)',
        [parent['activity_id'], data.get('label', ''), data.get('tag'),
         float(data.get('scoreMaximum', 100))]
    ).lastrowid
    db.commit()
    return jsonify({
        'id':           base + f'/lti/ags/lineitems/{new_id}',
        'label':        data.get('label', ''),
        'tag':          data.get('tag'),
        'scoreMaximum': float(data.get('scoreMaximum', 100)),
    }), 201


# ── AGS: score submission ─────────────────────────────────────────────────────

@app.route('/lti/ags/lineitems/<int:lineitem_id>/scores', methods=['POST'])
def ags_scores(lineitem_id):
    db = get_db()
    if not _ags_bearer_auth(db):
        return 'Unauthorized', 401

    li = db.execute('SELECT * FROM lineitems WHERE id=?', [lineitem_id]).fetchone()
    if not li:
        return 'Not Found', 404

    data              = request.get_json(force=True)
    user_id           = int(data['userId'])
    score_given       = float(data['scoreGiven'])    if data.get('scoreGiven')   is not None else None
    score_maximum     = float(data['scoreMaximum'])  if data.get('scoreMaximum') is not None else None
    activity_progress = data.get('activityProgress', '')
    grading_progress  = data.get('gradingProgress', '')
    timestamp         = data.get('timestamp', '')
    if not timestamp:
        return jsonify({'error': 'Missing timestamp'}), 400
    # tool_event_id: prefer Tool's explicit submissionId; fall back to timestamp string.
    # This value is stored and echoed back in SR launches via custom.tool_event_id so
    # the Tool can locate the exact attempt row without any LMS‑side ID mapping.
    tool_event_id = (data.get('submissionId') or '').strip() or timestamp
    if len(tool_event_id) > 64:
        return jsonify({'error': 'submissionId exceeds 64 characters'}), 400

    # Idempotency: (lineitem_id, user_id, tool_event_id) is unique.
    existing = db.execute(
        'SELECT 1 FROM score_events WHERE lineitem_id=? AND user_id=? AND tool_event_id=?',
        [lineitem_id, user_id, tool_event_id]
    ).fetchone()
    if not existing:
        db.execute(
            'INSERT INTO score_events '
            '(lineitem_id, user_id, score_given, score_maximum, '
            ' activity_progress, grading_progress, timestamp, tool_event_id) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            [lineitem_id, user_id, score_given, score_maximum,
             activity_progress, grading_progress, timestamp, tool_event_id]
        )
        db.commit()

    # Update the gradebook score on FullyGraded events targeting the default
    # ('score' or untagged) lineitem. Sub-lineitems with custom tag don't move
    # the main grade — they're auxiliary.
    if (grading_progress == 'FullyGraded'
            and li['tag'] in (None, 'score')
            and score_given is not None and score_maximum):
        score = score_given / score_maximum
        db.execute(
            'UPDATE grades SET score=?, updated_at=CURRENT_TIMESTAMP '
            'WHERE user_id=? AND activity_id=?',
            [score, user_id, li['activity_id']]
        )
        db.commit()

    return '', 204


# ── AGS: results (grades read-back) ──────────────────────────────────────────

@app.route('/lti/ags/lineitems/<int:lineitem_id>/results', methods=['GET'])
def ags_results(lineitem_id):
    db = get_db()
    if not _ags_bearer_auth(db):
        return 'Unauthorized', 401

    li = db.execute('SELECT * FROM lineitems WHERE id=?', [lineitem_id]).fetchone()
    if not li:
        return 'Not Found', 404

    rows = db.execute(
        'SELECT user_id, score FROM grades WHERE activity_id=? AND score IS NOT NULL',
        [li['activity_id']]
    ).fetchall()

    base = request.host_url.rstrip('/')
    score_max = li['score_maximum'] or 100
    results = [{
        'id':            base + f'/lti/ags/lineitems/{lineitem_id}/results/{r["user_id"]}',
        'scoreOf':       base + f'/lti/ags/lineitems/{lineitem_id}',
        'userId':        str(r['user_id']),
        'resultScore':   round(r['score'] * score_max, 4),
        'resultMaximum': score_max,
    } for r in rows]
    return jsonify(results)


# ── NRPS: course memberships ──────────────────────────────────────────────────

@app.route('/lti/nrps/<int:course_id>/memberships', methods=['GET'])
def nrps_memberships(course_id):
    db = get_db()
    if not _ags_bearer_auth(db):
        return 'Unauthorized', 401

    course = db.execute('SELECT * FROM courses WHERE id=?', [course_id]).fetchone()
    if not course:
        return 'Not Found', 404

    users = db.execute(
        'SELECT DISTINCT u.id, u.username FROM users u '
        'JOIN grades g ON g.user_id = u.id WHERE g.course_id=?',
        [course_id]
    ).fetchall()

    memberships_url = request.host_url.rstrip('/') + f'/lti/nrps/{course_id}/memberships'
    return jsonify({
        'id':      memberships_url,
        'context': {'id': str(course_id), 'title': course['name']},
        'members': [{
            'status':     'Active',
            'name':       u['username'],
            'given_name': u['username'],
            'user_id':    str(u['id']),
            'roles':      [ROLE_LEARNER],
        } for u in users],
    })


# ── Platform config endpoint (handy for Tool onboarding) ─────────────────────

@app.route('/api/lti/platform-config')
def platform_config():
    base = request.host_url.rstrip('/')
    return jsonify({
        'issuer':        base,
        'jwks_url':      base + '/lti/jwks',
        'oidc_auth_url': base + '/lti/oidc/auth',
        'token_url':     base + '/lti/token',
    })


@app.route('/.well-known/openid-configuration')
def oidc_config():
    base = request.host_url.rstrip('/')
    return jsonify({
        'issuer':                 base,
        'jwks_uri':               base + '/lti/jwks',
        'authorization_endpoint': base + '/lti/oidc/auth',
        'token_endpoint':         base + '/lti/token',
    })


import jwt  # noqa – used in lti_token for unverified peek of client_assertion


if __name__ == '__main__':
    init_db()
    migrate_db()
    app.run(host='0.0.0.0', port=8001, debug=True)
