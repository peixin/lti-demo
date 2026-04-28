"""LTI 1.3 / OIDC utilities — platform side."""
import base64, json, time, uuid

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


def make_id_token(private_pem, kid, iss, aud, sub, nonce,
                  deployment_id, resource_link_id, resource_link_title,
                  context_id, user_name, target_link_uri,
                  lineitem_url, return_url):
    """Build and sign LTI 1.3 id_token JWT."""
    now = int(time.time())
    claims = {
        'iss': iss, 'sub': sub, 'aud': aud,
        'iat': now, 'exp': now + 300, 'nonce': nonce,
        'name': user_name, 'given_name': user_name,
        'https://purl.imsglobal.org/spec/lti/claim/message_type':
            'LtiResourceLinkRequest',
        'https://purl.imsglobal.org/spec/lti/claim/version': '1.3.0',
        'https://purl.imsglobal.org/spec/lti/claim/deployment_id': deployment_id,
        'https://purl.imsglobal.org/spec/lti/claim/target_link_uri': target_link_uri,
        'https://purl.imsglobal.org/spec/lti/claim/resource_link': {
            'id': resource_link_id, 'title': resource_link_title,
        },
        'https://purl.imsglobal.org/spec/lti/claim/roles': [
            'http://purl.imsglobal.org/vocab/lis/v2/membership#Learner',
        ],
        'https://purl.imsglobal.org/spec/lti/claim/context': {
            'id': context_id,
            'type': ['http://purl.imsglobal.org/vocab/lis/v2/course#CourseOffering'],
        },
        'https://purl.imsglobal.org/spec/lti-ags/claim/endpoint': {
            'scope': [
                'https://purl.imsglobal.org/spec/lti-ags/scope/lineitem',
                'https://purl.imsglobal.org/spec/lti-ags/scope/score',
            ],
            'lineitems': lineitem_url,
            'lineitem': lineitem_url,
        },
        'https://purl.imsglobal.org/spec/lti/claim/launch_presentation': {
            'return_url': return_url,
        },
    }
    return jwt.encode(claims, private_pem, algorithm='RS256',
                      headers={'kid': kid})


def verify_tool_jwt(token, tool_jwks_url, token_endpoint_url):
    """Verify JWT Bearer assertion from tool at token endpoint."""
    import requests as req
    jwks = req.get(tool_jwks_url, timeout=5).json()
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
                      audience=token_endpoint_url)
