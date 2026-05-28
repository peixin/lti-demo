"""LTI 1.3 / OIDC utilities — tool side."""
import base64, time, uuid
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
from jwt.algorithms import RSAAlgorithm


def generate_key_pair():
    """Generate RSA-2048 key pair. Returns (private_pem, public_pem, kid)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem, uuid.uuid4().hex[:16]


def public_key_to_jwk(public_pem, kid):
    """Convert PEM public key to JWK dict."""
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    pub = load_pem_public_key(public_pem.encode())
    nums = pub.public_numbers()

    def _b64(n):
        b = n.to_bytes((n.bit_length() + 7) // 8, 'big')
        return base64.urlsafe_b64encode(b).rstrip(b'=').decode()

    return {'kty': 'RSA', 'use': 'sig', 'alg': 'RS256',
            'kid': kid, 'n': _b64(nums.n), 'e': _b64(nums.e)}


def verify_id_token(token, platform_jwks_url, client_id, platform_iss):
    """Verify platform's id_token JWT. Returns claims or raises."""
    import requests as req
    jwks = req.get(platform_jwks_url, timeout=5).json()
    header = jwt.get_unverified_header(token)
    kid = header.get('kid')
    key = None
    for k in jwks.get('keys', []):
        if k.get('kid') == kid:
            key = RSAAlgorithm.from_jwk(k)
            break
    if not key:
        raise ValueError(f'No JWKS key found for kid={kid!r}')
    return jwt.decode(token, key, algorithms=['RS256'],
                      audience=client_id, issuer=platform_iss)


def iso_utc_now():
    """ISO 8601 UTC timestamp (seconds precision, 'Z' suffix)."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def get_access_token(platform_token_url, private_pem, kid, client_id,
                     scope='https://purl.imsglobal.org/spec/lti-ags/scope/score'):
    """Request AGS access token from platform via JWT Bearer grant."""
    import requests as req

    now = int(time.time())
    assertion = jwt.encode({
        'iss': client_id, 'sub': client_id,
        'aud': platform_token_url,
        'iat': now, 'exp': now + 300,
        'jti': uuid.uuid4().hex,
    }, private_pem, algorithm='RS256', headers={'kid': kid})

    resp = req.post(platform_token_url, data={
        'grant_type': 'client_credentials',
        'client_assertion_type':
            'urn:ietf:params:oauth:client-assertion-type:jwt-bearer',
        'client_assertion': assertion,
        'scope': scope,
    }, timeout=10)
    resp.raise_for_status()
    return resp.json()['access_token']


def post_score(access_token, lineitem_url, user_id, score_given, score_maximum,
               timestamp, activity_progress='Completed',
               grading_progress='FullyGraded', submission_id=None):
    """Submit a score event to platform via AGS.

    `score_given` / `score_maximum` use Tool's raw scale (do NOT normalize to 100).
    `timestamp` should be a unique ISO 8601 UTC string per attempt
    (use the same value on retries for idempotency).
    `submission_id` is the Tool's own stable attempt identifier (≤ 64 chars).
    The platform stores it as tool_event_id and echoes it back in SR launches
    via custom.tool_event_id, so the Tool can look up the exact attempt row.
    """
    import requests as req

    body = {
        'userId':           str(user_id),
        'scoreGiven':       score_given,
        'scoreMaximum':     score_maximum,
        'activityProgress': activity_progress,
        'gradingProgress':  grading_progress,
        'timestamp':        timestamp,
    }
    if submission_id:
        body['submissionId'] = submission_id

    resp = req.post(
        lineitem_url.rstrip('/') + '/scores',
        json=body,
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type':  'application/vnd.ims.lis.v1.score+json',
        },
        timeout=10,
    )
    resp.raise_for_status()


def make_dl_response_jwt(private_pem, kid, client_id, platform_iss,
                         deployment_id, data, content_items):
    """Sign an LtiDeepLinkingResponse JWT to form-POST back to the platform.

    `data` must be echoed back unchanged from the request's deep_linking_settings.
    `content_items` should be a list with a single ltiResourceLink dict.
    """
    now = int(time.time())
    claims = {
        'iss':   client_id,
        'sub':   client_id,
        'aud':   platform_iss,
        'iat':   now,
        'exp':   now + 300,
        'nonce': uuid.uuid4().hex,
        'https://purl.imsglobal.org/spec/lti/claim/message_type':
            'LtiDeepLinkingResponse',
        'https://purl.imsglobal.org/spec/lti/claim/deployment_id': deployment_id,
        'https://purl.imsglobal.org/spec/lti/claim/version':       '1.3.0',
        'https://purl.imsglobal.org/spec/lti-dl/claim/data':          data,
        'https://purl.imsglobal.org/spec/lti-dl/claim/content_items': content_items,
    }
    return jwt.encode(claims, private_pem, algorithm='RS256',
                      headers={'kid': kid})
