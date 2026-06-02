import json, os, sqlite3, urllib.parse, uuid

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from lti import (
    generate_key_pair,
    get_access_token,
    iso_utc_now,
    make_dl_response_jwt,
    post_score,
    public_key_to_jwk,
    verify_id_token,
)

from werkzeug.middleware.proxy_fix import ProxyFix

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.secret_key = os.environ.get("SECRET_KEY", "exam-tool-demo-secret-change-in-prod")
app.config["SESSION_COOKIE_NAME"] = "examtool_session"

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
DATABASE = os.environ.get(
    "DATABASE", os.path.join(os.path.dirname(__file__), "data/exam-tool.db")
)

# LTI claim short-name aliases (keeps SQL/business code readable)
C_MESSAGE_TYPE = "https://purl.imsglobal.org/spec/lti/claim/message_type"
C_DEPLOYMENT_ID = "https://purl.imsglobal.org/spec/lti/claim/deployment_id"
C_TARGET_LINK_URI = "https://purl.imsglobal.org/spec/lti/claim/target_link_uri"
C_RESOURCE_LINK = "https://purl.imsglobal.org/spec/lti/claim/resource_link"
C_CONTEXT = "https://purl.imsglobal.org/spec/lti/claim/context"
C_ROLES = "https://purl.imsglobal.org/spec/lti/claim/roles"
C_CUSTOM = "https://purl.imsglobal.org/spec/lti/claim/custom"
C_FOR_USER = "https://purl.imsglobal.org/spec/lti/claim/for_user"
C_LAUNCH_PRES = "https://purl.imsglobal.org/spec/lti/claim/launch_presentation"
C_AGS_ENDPOINT = "https://purl.imsglobal.org/spec/lti-ags/claim/endpoint"
C_DL_SETTINGS = "https://purl.imsglobal.org/spec/lti-dl/claim/deep_linking_settings"
ROLE_INSTRUCTOR = "http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor"

SCHEMA = """
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
    category         TEXT NOT NULL,
    lineitem_url     TEXT,
    return_url       TEXT,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS questions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    text     TEXT NOT NULL,
    options  TEXT NOT NULL,
    answer   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    category      TEXT NOT NULL,
    answers       TEXT NOT NULL,
    score_given   REAL NOT NULL,
    score_maximum REAL NOT NULL,
    timestamp     TEXT NOT NULL,
    submitted_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

CATEGORIES = {
    "art": {"name": "艺术", "emoji": "🎨"},
    "cs": {"name": "计算机", "emoji": "💻"},
    "history": {"name": "历史", "emoji": "📜"},
}

SAMPLE_QUESTIONS = {}
QUESTIONS_FILE = os.path.join(os.path.dirname(__file__), "questions.json")
try:
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        _qdata = json.load(f)
        for cat, qs in _qdata.items():
            SAMPLE_QUESTIONS[cat] = []
            for item in qs:
                SAMPLE_QUESTIONS[cat].append(
                    (item["text"], item["options"], item["answer"])
                )
except Exception as e:
    print(f"Error loading questions.json: {e}")


# ── i18n Translations ─────────────────────────────────────────────────────────

TRANSLATIONS = {}
LOCALES_DIR = os.path.join(os.path.dirname(__file__), "locales")
try:
    for filename in os.listdir(LOCALES_DIR):
        if filename.endswith(".json"):
            lang_code = filename[:-5]  # e.g., 'zh-CN'
            filepath = os.path.join(LOCALES_DIR, filename)
            with open(filepath, "r", encoding="utf-8") as f:
                TRANSLATIONS[lang_code] = json.load(f)
except Exception as e:
    print(f"Error loading translation files: {e}")


def get_lang():
    lang = request.args.get("lang")
    if lang in TRANSLATIONS:
        session["lang"] = lang
        return lang
    if "lang" in session:
        return session["lang"]
    al = request.headers.get("Accept-Language", "")
    if al:
        best = request.accept_languages.best_match(
            ["ko", "zh-CN", "zh-TW", "zh-HK", "en"]
        )
        if best:
            if best in ["zh-HK", "zh-TW"]:
                return "zh-TW"
            if best == "zh-CN" or best == "zh":
                return "zh-CN"
            return best
    return "zh-CN"


def py_t(key, **kwargs):
    lang = get_lang()
    lang_dict = TRANSLATIONS.get(lang, TRANSLATIONS.get("zh-CN", {}))
    val = lang_dict.get(key, TRANSLATIONS.get("zh-CN", {}).get(key, key))
    try:
        return val.format(**kwargs)
    except Exception:
        return val


@app.route("/set-lang/<lang>")
def set_lang(lang):
    if lang in TRANSLATIONS:
        session["lang"] = lang
    # Use explicit `next` param instead of request.referrer.
    # In iframe mode, referrer is the parent (LMS) page URL, which would
    # navigate the iframe away from the Tool and may return 405 / auth errors.
    next_page = request.args.get("next") or url_for("exam")
    # Safety: only allow relative paths to prevent open-redirect.
    if next_page.startswith("http"):
        next_page = url_for("exam")
    # Append ?lang= so the language persists even if the session cookie is
    # blocked by the browser's SameSite policy inside a cross-origin iframe.
    sep = "&" if "?" in next_page else "?"
    return redirect(f"{next_page}{sep}lang={lang}")


@app.context_processor
def inject_translations():
    lang = get_lang()

    def t(key, **kwargs):
        return py_t(key, **kwargs)

    return dict(t=t, current_lang=lang, TRANSLATIONS=TRANSLATIONS)


# ── DB ────────────────────────────────────────────────────────────────────────


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA synchronous=NORMAL")
    return g.db


@app.teardown_appcontext
def close_db(_=None):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    """Create tables, drop legacy demo data that doesn't match the new schema, seed samples."""
    with app.app_context():
        db = get_db()
        # Migration: if old `questions` table has no `category` column,
        # drop demo data tables so the new schema can be applied cleanly.
        existing = {
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "questions" in existing:
            cols = {
                row[1] for row in db.execute("PRAGMA table_info(questions)").fetchall()
            }
            if "category" not in cols:
                db.executescript(
                    "DROP TABLE IF EXISTS questions;"
                    "DROP TABLE IF EXISTS attempts;"
                    "DROP TABLE IF EXISTS lti_sessions;"
                )
                db.commit()

        db.executescript(SCHEMA)
        if not db.execute("SELECT 1 FROM tool_config").fetchone():
            priv, pub, kid = generate_key_pair()
            db.execute(
                "INSERT INTO tool_config (id, kid, private_key_pem, public_key_pem) "
                "VALUES (1, ?, ?, ?)",
                [kid, priv, pub],
            )
        if not db.execute("SELECT 1 FROM questions").fetchone():
            for cat, qs in SAMPLE_QUESTIONS.items():
                for text, opts, ans in qs:
                    db.execute(
                        "INSERT INTO questions (category, text, options, answer) "
                        "VALUES (?, ?, ?, ?)",
                        [cat, text, json.dumps(opts, ensure_ascii=False), ans],
                    )
        db.commit()


# ── Admin ─────────────────────────────────────────────────────────────────────


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["admin"] = True
            return redirect(url_for("admin"))
        flash(py_t("wrong_pwd"), "danger")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    return redirect(url_for("admin_login"))


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    db = get_db()
    config = db.execute("SELECT * FROM tool_config WHERE id=1").fetchone()

    if request.method == "POST":
        db.execute(
            "UPDATE tool_config SET platform_iss=?, client_id=?, deployment_id=?, "
            "platform_oidc_auth_url=?, platform_jwks_url=?, platform_token_url=? "
            "WHERE id=1",
            [
                request.form.get("platform_iss", "").strip(),
                request.form.get("client_id", "").strip(),
                request.form.get("deployment_id", "").strip(),
                request.form.get("platform_oidc_auth_url", "").strip(),
                request.form.get("platform_jwks_url", "").strip(),
                request.form.get("platform_token_url", "").strip(),
            ],
        )
        db.commit()
        flash("Platform config saved", "success")
        config = db.execute("SELECT * FROM tool_config WHERE id=1").fetchone()

    raw_attempts = db.execute(
        "SELECT a.id, a.category, a.score_given, a.score_maximum, a.answers, "
        "       a.submitted_at, s.user_name, s.resource_link_id "
        "FROM attempts a JOIN lti_sessions s ON a.session_id=s.session_id "
        "ORDER BY a.submitted_at DESC"
    ).fetchall()
    raw_qs = db.execute(
        "SELECT id, category, text, answer, options FROM questions ORDER BY category, id"
    ).fetchall()
    questions = [
        {
            "id": q["id"],
            "category": q["category"],
            "text": q["text"],
            "answer": q["answer"],
            "options": json.loads(q["options"]),
        }
        for q in raw_qs
    ]
    qs_by_id = {q["id"]: q for q in questions}
    attempts = []
    for a in raw_attempts:
        ans = json.loads(a["answers"]) if a["answers"] else {}
        cat_qs = [q for q in questions if q["category"] == a["category"]]
        detail = [
            {
                "text": q["text"],
                "options": q["options"],
                "correct": q["answer"],
                "chosen": ans.get(str(q["id"]), -1),
            }
            for q in cat_qs
        ]
        pct = (a["score_given"] / a["score_maximum"]) if a["score_maximum"] else 0
        attempts.append({**dict(a), "detail": detail, "pct": pct})

    qs_by_category = {}
    for q in questions:
        qs_by_category.setdefault(q["category"], []).append(q)

    base_url = request.host_url.rstrip("/")
    tool_info = {
        "login_url": base_url + "/lti/login",
        "redirect_uri": base_url + "/lti/launch",
        "jwks_url": base_url + "/lti/jwks",
        "target_link_uri": base_url + "/exam",
    }
    return render_template(
        "admin.html",
        config=config,
        attempts=attempts,
        categories=CATEGORIES,
        qs_by_category=qs_by_category,
        tool_info=tool_info,
    )


@app.route("/admin/attempts")
def admin_attempts():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    db = get_db()

    # ── Query params ──────────────────────────────────────────────────────────
    q_name     = request.args.get("name", "").strip()
    q_category = request.args.get("category", "").strip()
    page       = max(1, int(request.args.get("page", 1)))
    per_page   = 20

    # ── Build WHERE clause ────────────────────────────────────────────────────
    where_parts = []
    params: list = []
    if q_name:
        where_parts.append("s.user_name LIKE ?")
        params.append(f"%{q_name}%")
    if q_category and q_category in CATEGORIES:
        where_parts.append("a.category = ?")
        params.append(q_category)

    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

    base_sql = (
        "FROM attempts a JOIN lti_sessions s ON a.session_id=s.session_id "
        + where_sql
    )

    total = db.execute("SELECT COUNT(*) " + base_sql, params).fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    offset = (page - 1) * per_page

    rows = db.execute(
        "SELECT a.id, a.category, a.score_given, a.score_maximum, "
        "       a.submitted_at, s.user_name, s.resource_link_id "
        + base_sql
        + " ORDER BY a.submitted_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    ).fetchall()

    attempts = []
    for a in rows:
        pct = (a["score_given"] / a["score_maximum"]) if a["score_maximum"] else 0
        attempts.append({**dict(a), "pct": pct})

    return render_template(
        "admin_attempts.html",
        attempts=attempts,
        categories=CATEGORIES,
        total=total,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        q_name=q_name,
        q_category=q_category,
    )


@app.route("/admin/attempts/delete", methods=["POST"])
def admin_attempts_delete():
    if not session.get("admin"):
        return redirect(url_for("admin_login"))

    ids = request.form.getlist("ids")
    if ids:
        db = get_db()
        placeholders = ",".join("?" * len(ids))
        db.execute(f"DELETE FROM attempts WHERE id IN ({placeholders})", ids)
        db.commit()
        flash(py_t("attempts_deleted", count=len(ids)), "success")

    # Preserve search params when redirecting back
    back = request.form.get("back_url", url_for("admin_attempts"))
    return redirect(back)


# ── Tool JWKS ─────────────────────────────────────────────────────────────────


@app.route("/lti/jwks")
def lti_jwks():
    config = get_db().execute("SELECT * FROM tool_config WHERE id=1").fetchone()
    return jsonify(
        {"keys": [public_key_to_jwk(config["public_key_pem"], config["kid"])]}
    )


# ── LTI 1.3 Step 2: Login initiation ─────────────────────────────────────────


@app.route("/lti/login", methods=["GET", "POST"])
def lti_login():
    get = lambda k: request.values.get(k, "")

    state = uuid.uuid4().hex
    nonce = uuid.uuid4().hex
    db = get_db()
    db.execute("INSERT INTO oidc_state (state, nonce) VALUES (?, ?)", [state, nonce])
    db.execute(
        "DELETE FROM oidc_state WHERE created_at < datetime('now', '-10 minutes')"
    )
    db.commit()

    config = db.execute("SELECT * FROM tool_config WHERE id=1").fetchone()
    if not config or not config["platform_oidc_auth_url"]:
        return render_template("error.html", message=py_t("not_configured"))

    redirect_uri = request.host_url.rstrip("/") + "/lti/launch"
    auth_params = urllib.parse.urlencode(
        {
            "scope": "openid",
            "response_type": "id_token",
            "client_id": config["client_id"],
            "redirect_uri": redirect_uri,
            "login_hint": get("login_hint"),
            "lti_message_hint": get("lti_message_hint"),
            "state": state,
            "nonce": nonce,
            "response_mode": "form_post",
            "prompt": "none",
        }
    )
    return redirect(config["platform_oidc_auth_url"] + "?" + auth_params)


# ── LTI 1.3 Step 4: OIDC callback — validate id_token & dispatch by message_type ──


@app.route("/lti/launch", methods=["POST"])
def lti_launch():
    id_token = request.form.get("id_token", "")
    state = request.form.get("state", "")

    db = get_db()
    stored = db.execute("SELECT * FROM oidc_state WHERE state=?", [state]).fetchone()
    if not stored:
        return render_template("error.html", message=py_t("state_expired")), 400

    db.execute("DELETE FROM oidc_state WHERE state=?", [state])
    db.commit()

    config = db.execute("SELECT * FROM tool_config WHERE id=1").fetchone()
    try:
        claims = verify_id_token(
            id_token,
            config["platform_jwks_url"],
            config["client_id"],
            config["platform_iss"],
        )
    except Exception as e:
        return render_template(
            "error.html", message=f"JWT verification failed: {e}"
        ), 403

    if claims.get("nonce") != stored["nonce"]:
        return render_template("error.html", message="Nonce mismatch."), 400

    dep_id = claims.get(C_DEPLOYMENT_ID, "")
    if dep_id != config["deployment_id"]:
        return render_template("error.html", message="Unknown deployment_id."), 400

    msg_type = claims.get(C_MESSAGE_TYPE, "")
    if msg_type == "LtiDeepLinkingRequest":
        return handle_deep_linking_request(claims)
    if msg_type == "LtiSubmissionReviewRequest":
        return handle_submission_review(claims)
    if msg_type == "LtiResourceLinkRequest":
        return handle_resource_link(claims)
    return render_template(
        "error.html", message=f"Unsupported message_type: {msg_type!r}"
    ), 400


# ── ResourceLinkRequest: student / teacher launches the activity ─────────────


def _category_from_claims(claims):
    """Pull category from custom claim (preferred) or parse target_link_uri ?category=."""
    custom = claims.get(C_CUSTOM) or {}
    cat = custom.get("category")
    if cat:
        return cat
    tlu = claims.get(C_TARGET_LINK_URI, "")
    parsed = urllib.parse.urlparse(tlu)
    cat = (urllib.parse.parse_qs(parsed.query).get("category") or [None])[0]
    return cat


def handle_resource_link(claims):
    db = get_db()
    rl = claims.get(C_RESOURCE_LINK) or {}
    ctx = claims.get(C_CONTEXT) or {}
    ags = claims.get(C_AGS_ENDPOINT) or {}
    lp = claims.get(C_LAUNCH_PRES) or {}

    category = _category_from_claims(claims)
    if not category or category not in CATEGORIES:
        # No DeepLink configuration → tell the user / teacher to run DL first.
        return render_template(
            "need_deeplink.html",
            user_name=claims.get("name", "同学"),
            return_url=lp.get("return_url"),
        )

    sess_id = uuid.uuid4().hex
    db.execute(
        "INSERT INTO lti_sessions "
        "(session_id, sub, user_name, deployment_id, resource_link_id, "
        " context_id, category, lineitem_url, return_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            sess_id,
            claims.get("sub"),
            claims.get("name", "Learner"),
            claims.get(C_DEPLOYMENT_ID, ""),
            rl.get("id", ""),
            ctx.get("id", ""),
            category,
            ags.get("lineitem", ""),
            lp.get("return_url") or "",
        ],
    )
    db.commit()
    # A learner/instructor arriving through LTI must not inherit a local admin
    # login that may linger in this browser's session cookie.
    session.pop("admin", None)
    session["lti_session_id"] = sess_id
    return redirect(url_for("exam", lti_session_id=sess_id))


# ── SubmissionReviewRequest: read-only review of a user's attempt ────────────


def handle_submission_review(claims):
    db = get_db()
    rl = claims.get(C_RESOURCE_LINK) or {}
    for_user = claims.get(C_FOR_USER) or {}
    custom = claims.get(C_CUSTOM) or {}
    lp = claims.get(C_LAUNCH_PRES) or {}

    target_sub = for_user.get("user_id") or claims.get("sub")
    target_name = for_user.get("name") or claims.get("name", "同学")
    resource_link_id = rl.get("id", "")

    # Three-level lookup strategy for tool_event_id:
    #
    # Level 1 — new data (after fix): submissionId = sess_id (UUID)
    #   tool_event_id == session_id → direct match on attempts.session_id
    #
    # Level 2 — old data (before fix): platform had no submissionId, fell back
    #   to storing timestamp as tool_event_id → match on attempts.timestamp
    #
    # Level 3 — nothing found: show the user's most-recent attempt for the activity
    tool_event_id = custom.get("tool_event_id")
    attempt = None
    if tool_event_id:
        # Level 1: tool_event_id == session_id (UUID)
        attempt = db.execute(
            "SELECT * FROM attempts WHERE session_id=? ORDER BY id DESC LIMIT 1",
            [tool_event_id],
        ).fetchone()
    if not attempt and tool_event_id:
        # Level 2: tool_event_id == timestamp (old fallback)
        attempt = db.execute(
            "SELECT a.* FROM attempts a JOIN lti_sessions s ON a.session_id=s.session_id "
            "WHERE a.timestamp=? AND s.sub=? AND s.resource_link_id=?",
            [tool_event_id, target_sub, resource_link_id],
        ).fetchone()
    if not attempt:
        # Level 3: show most-recent attempt for this user × activity
        attempt = db.execute(
            "SELECT a.* FROM attempts a JOIN lti_sessions s ON a.session_id=s.session_id "
            "WHERE s.sub=? AND s.resource_link_id=? ORDER BY a.id DESC LIMIT 1",
            [target_sub, resource_link_id],
        ).fetchone()
    if not attempt:
        return render_template(
            "error.html", message=py_t("no_submission", name=target_name)
        ), 404

    # Redirect to a stable GET URL so the browser's address bar shows a
    # navigable path. This prevents the lang-switcher from sending users
    # back to /lti/launch (a POST-only endpoint) and getting a 405.
    return_url = lp.get("return_url") or ""
    return redirect(
        url_for(
            "exam_review",
            attempt_id=attempt["id"],
            user_name=target_name,
            return_url=return_url,
        )
    )


# ── Submission Review: stable GET endpoint ───────────────────────────────────


@app.route("/exam/review/<int:attempt_id>")
def exam_review(attempt_id):
    db = get_db()
    attempt = db.execute("SELECT * FROM attempts WHERE id=?", [attempt_id]).fetchone()
    if not attempt:
        return render_template(
            "error.html", message=py_t("no_submission", name="?")
        ), 404

    rows = db.execute(
        "SELECT * FROM questions WHERE category=? ORDER BY id", [attempt["category"]]
    ).fetchall()
    answers = json.loads(attempt["answers"])
    questions = []
    for q in rows:
        questions.append(
            {
                "text": q["text"],
                "options": json.loads(q["options"]),
                "chosen": answers.get(str(q["id"]), -1),
                "correct": q["answer"],
            }
        )
    score = (
        attempt["score_given"] / attempt["score_maximum"]
        if attempt["score_maximum"]
        else 0
    )
    return render_template(
        "detail.html",
        user_name=request.args.get("user_name", ""),
        category=attempt["category"],
        score=score,
        score_given=attempt["score_given"],
        score_maximum=attempt["score_maximum"],
        questions=questions,
        return_url=request.args.get("return_url", ""),
        submitted_at=attempt["submitted_at"],
    )


# ── DeepLinkingRequest: teacher picks a question category ────────────────────


def handle_deep_linking_request(claims):
    dl = claims.get(C_DL_SETTINGS) or {}
    deep_link_return_url = dl.get("deep_link_return_url", "")
    data = dl.get("data", "")
    if not deep_link_return_url:
        return render_template(
            "error.html", message="Missing deep_link_return_url in DeepLinkingRequest."
        ), 400

    cats_with_count = {
        key: {**info, "count": len(SAMPLE_QUESTIONS.get(key, []))}
        for key, info in CATEGORIES.items()
    }
    return render_template(
        "pick_category.html",
        categories=cats_with_count,
        deep_link_return_url=deep_link_return_url,
        data=data,
        user_name=claims.get("name", "老师"),
    )


@app.route("/lti/deeplink/submit", methods=["POST"])
def deeplink_submit():
    category = request.form.get("category", "")
    deep_link_return_url = request.form.get("deep_link_return_url", "")
    data = request.form.get("data", "")

    if category not in CATEGORIES:
        return render_template("error.html", message="Invalid category."), 400
    if not deep_link_return_url:
        return render_template("error.html", message="Missing return URL."), 400

    db = get_db()
    config = db.execute("SELECT * FROM tool_config WHERE id=1").fetchone()

    base_url = request.host_url.rstrip("/")
    cat_info = CATEGORIES[category]
    content_item = {
        "type": "ltiResourceLink",
        "url": f"{base_url}/exam?category={category}",
        "title": f"{cat_info['emoji']} {cat_info['name']}题集",
        "custom": {
            "category": category,
        },
    }

    jwt_token = make_dl_response_jwt(
        private_pem=config["private_key_pem"],
        kid=config["kid"],
        client_id=config["client_id"],
        platform_iss=config["platform_iss"],
        deployment_id=config["deployment_id"],
        data=data,
        content_items=[content_item],
    )
    return render_template(
        "dl_response_form.html",
        deep_link_return_url=deep_link_return_url,
        jwt=jwt_token,
        category_label=f"{cat_info['emoji']} {cat_info['name']}",
    )


# ── Exam ──────────────────────────────────────────────────────────────────────


@app.route("/exam")
def exam():
    sess_id = request.args.get("lti_session_id") or session.get("lti_session_id")
    if not sess_id:
        return render_template("error.html", message=py_t("no_session"))
    db = get_db()
    lti_sess = db.execute(
        "SELECT * FROM lti_sessions WHERE session_id=?", [sess_id]
    ).fetchone()
    if not lti_sess:
        return render_template("error.html", message=py_t("session_expired"))
    if db.execute("SELECT 1 FROM attempts WHERE session_id=?", [sess_id]).fetchone():
        return redirect(url_for("result"))

    category = lti_sess["category"]
    cat_info = CATEGORIES.get(category)
    if not cat_info:
        return render_template(
            "need_deeplink.html",
            user_name=lti_sess["user_name"],
            return_url=lti_sess["return_url"],
        )

    rows = db.execute(
        "SELECT id, text, options FROM questions WHERE category=? ORDER BY id",
        [category],
    ).fetchall()
    questions = [
        {"id": r["id"], "text": r["text"], "options": json.loads(r["options"])}
        for r in rows
    ]
    return render_template(
        "exam.html",
        user_name=lti_sess["user_name"],
        category=category,
        questions=questions,
        lti_session_id=sess_id,
    )


@app.route("/exam/submit", methods=["POST"])
def submit_exam():
    sess_id = (
        request.args.get("lti_session_id")
        or request.form.get("lti_session_id")
        or session.get("lti_session_id")
    )
    if not sess_id:
        return render_template("error.html", message=py_t("no_session"))
    db = get_db()
    lti_sess = db.execute(
        "SELECT * FROM lti_sessions WHERE session_id=?", [sess_id]
    ).fetchone()
    if not lti_sess:
        return render_template("error.html", message=py_t("session_expired"))

    category = lti_sess["category"]
    questions = db.execute(
        "SELECT * FROM questions WHERE category=? ORDER BY id", [category]
    ).fetchall()

    answers, correct = {}, 0
    for q in questions:
        raw = request.form.get(f"q_{q['id']}", "")
        chosen = int(raw) if raw.isdigit() else -1
        answers[str(q["id"])] = chosen
        if chosen == q["answer"]:
            correct += 1
    score_given = float(correct)
    score_maximum = float(len(questions))
    timestamp = iso_utc_now()

    db.execute(
        "INSERT INTO attempts "
        "(session_id, category, answers, score_given, score_maximum, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [sess_id, category, json.dumps(answers), score_given, score_maximum, timestamp],
    )
    db.commit()

    # AGS score callback — only when platform sent an endpoint claim.
    if lti_sess["lineitem_url"]:
        config = db.execute("SELECT * FROM tool_config WHERE id=1").fetchone()
        try:
            token = get_access_token(
                config["platform_token_url"],
                config["private_key_pem"],
                config["kid"],
                config["client_id"],
            )
            # Raw scale (Tool's own grading scheme); LMS normalizes internally.
            # Use sess_id (UUID) as submissionId: unique, stable, and directly
            # maps to attempts.session_id — so SR lookup is a simple
            # WHERE session_id = tool_event_id with no JOIN needed.
            post_score(
                token,
                lti_sess["lineitem_url"],
                lti_sess["sub"],
                score_given=score_given,
                score_maximum=score_maximum,
                timestamp=timestamp,
                activity_progress="Completed",
                grading_progress="FullyGraded",
                submission_id=sess_id,
            )
        except Exception as e:
            # Surface the platform's actual response body — a bare
            # "500 Server Error for url" hides WHY the platform rejected us.
            resp = getattr(e, "response", None)
            if resp is not None:
                app.logger.warning(
                    "AGS score callback failed: %s %s\nrequest_url=%s\nresponse_body=%s",
                    resp.status_code, resp.reason, resp.url, resp.text,
                )
            else:
                app.logger.warning("AGS score callback failed: %s", e)

    return redirect(url_for("result", lti_session_id=sess_id))


@app.route("/result")
def result():
    sess_id = request.args.get("lti_session_id") or session.get("lti_session_id")
    if not sess_id:
        return render_template("error.html", message=py_t("no_session"))
    db = get_db()
    attempt = db.execute(
        "SELECT * FROM attempts WHERE session_id=? ORDER BY id DESC LIMIT 1", [sess_id]
    ).fetchone()
    lti_sess = db.execute(
        "SELECT * FROM lti_sessions WHERE session_id=?", [sess_id]
    ).fetchone()
    if not attempt:
        return redirect(url_for("exam"))
    cat_info = CATEGORIES.get(
        attempt["category"], {"name": attempt["category"], "emoji": ""}
    )
    pct = (
        attempt["score_given"] / attempt["score_maximum"]
        if attempt["score_maximum"]
        else 0
    )
    return render_template(
        "result.html",
        score=pct,
        score_given=attempt["score_given"],
        score_maximum=attempt["score_maximum"],
        category=attempt["category"],
        user_name=lti_sess["user_name"],
        ags_enabled=bool(lti_sess["lineitem_url"]),
        return_url=lti_sess["return_url"],
        submitted_at=attempt["submitted_at"],
    )


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 8002))
    app.run(host="0.0.0.0", port=port, debug=True)
