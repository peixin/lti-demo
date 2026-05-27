"""LTI 1.3 / OIDC utilities — platform side."""
import base64, time, uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
from jwt.algorithms import RSAAlgorithm


LTI    = 'https://purl.imsglobal.org/spec/lti/claim'
LTI_AGS = 'https://purl.imsglobal.org/spec/lti-ags/claim'
LTI_DL  = 'https://purl.imsglobal.org/spec/lti-dl/claim'
LTI_NRPS = 'https://purl.imsglobal.org/spec/lti-nrps/claim'
ROLE_LEARNER    = 'http://purl.imsglobal.org/vocab/lis/v2/membership#Learner'
ROLE_INSTRUCTOR = 'http://purl.imsglobal.org/vocab/lis/v2/membership#Instructor'


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


def make_id_token(*, private_pem, kid, iss, aud, sub, nonce, deployment_id,
                  message_type, user_name, target_link_uri,
                  resource_link=None, context=None, roles=None,
                  lineitem_url=None, return_url=None, custom=None,
                  for_user=None, deep_linking_settings=None, nrps_url=None):
    """Build and sign an LTI 1.3 id_token JWT.

    `message_type` selects the LTI message shape:
      - 'LtiResourceLinkRequest'      → student/teacher launches the activity
      - 'LtiDeepLinkingRequest'       → teacher configures content (DL flow)
      - 'LtiSubmissionReviewRequest'  → read-only review of a user's attempt
    """
    now = int(time.time())
    claims = {
        'iss': iss, 'sub': sub, 'aud': aud,
        'iat': now, 'exp': now + 300, 'nonce': nonce,
        'name': user_name, 'given_name': user_name,
        f'{LTI}/message_type':   message_type,
        f'{LTI}/version':        '1.3.0',
        f'{LTI}/deployment_id':  deployment_id,
        f'{LTI}/target_link_uri': target_link_uri,
        f'{LTI}/roles':          roles or [ROLE_LEARNER],
        f'{LTI}/launch_presentation': {
            'document_target': 'iframe',
            'return_url':      return_url,
        },
    }
    if context:
        claims[f'{LTI}/context'] = context
    # DeepLinkingRequest deliberately omits resource_link per spec.
    if resource_link and message_type != 'LtiDeepLinkingRequest':
        claims[f'{LTI}/resource_link'] = resource_link
    if lineitem_url:
        claims[f'{LTI_AGS}/endpoint'] = {
            'scope': [
                'https://purl.imsglobal.org/spec/lti-ags/scope/lineitem',
                'https://purl.imsglobal.org/spec/lti-ags/scope/lineitem.readonly',
                'https://purl.imsglobal.org/spec/lti-ags/scope/result.readonly',
                'https://purl.imsglobal.org/spec/lti-ags/scope/score',
            ],
            'lineitems': lineitem_url,
            'lineitem':  lineitem_url,
        }
    if nrps_url:
        claims[f'{LTI_NRPS}/namesroleservice'] = {
            'context_memberships_url': nrps_url,
            'service_versions': ['2.0'],
        }
    if for_user:
        claims[f'{LTI}/for_user'] = for_user
    if deep_linking_settings:
        claims[f'{LTI_DL}/deep_linking_settings'] = deep_linking_settings
    if custom:
        claims[f'{LTI}/custom'] = custom
    return jwt.encode(claims, private_pem, algorithm='RS256',
                      headers={'kid': kid})


def _load_jwk_from_jwks(token, jwks_url):
    """Fetch JWKS, find the key matching token's `kid`, return RSA key object."""
    import requests as req
    jwks = req.get(jwks_url, timeout=5).json()
    header = jwt.get_unverified_header(token)
    kid = header.get('kid')
    for k in jwks.get('keys', []):
        if k.get('kid') == kid:
            return RSAAlgorithm.from_jwk(k)
    raise ValueError(f'No JWKS key found for kid={kid!r}')


def verify_tool_jwt(token, tool_jwks_url, token_endpoint_url):
    """Verify a Tool's JWT-Bearer client_assertion at the token endpoint."""
    key = _load_jwk_from_jwks(token, tool_jwks_url)
    return jwt.decode(token, key, algorithms=['RS256'],
                      audience=token_endpoint_url)


def verify_dl_response(token, tool_jwks_url, expected_aud, expected_iss):
    """Verify a Tool's LtiDeepLinkingResponse JWT signature & claims."""
    key = _load_jwk_from_jwks(token, tool_jwks_url)
    claims = jwt.decode(token, key, algorithms=['RS256'],
                        audience=expected_aud)
    if claims.get('iss') != expected_iss:
        raise ValueError(f"iss mismatch (got {claims.get('iss')!r}, "
                         f"expected {expected_iss!r})")
    return claims
