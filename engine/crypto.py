"""AES-256-GCM + PBKDF2 encryption, compatible with the browser's WebCrypto API."""
import base64
import gzip
import json
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITER = 250_000


def _key(passphrase, salt, iters=ITER):
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iters).derive(passphrase.encode())


def encrypt(obj, passphrase):
    salt, iv = os.urandom(16), os.urandom(12)
    raw = gzip.compress(json.dumps(obj, separators=(",", ":")).encode(), mtime=0)
    ct = AESGCM(_key(passphrase, salt)).encrypt(iv, raw, None)
    b = lambda x: base64.b64encode(x).decode()
    return {"v": 1, "alg": "AES-GCM", "kdf": "PBKDF2-SHA256", "iter": ITER, "gzip": True,
            "salt": b(salt), "iv": b(iv), "ct": b(ct)}


def decrypt(blob, passphrase):
    u = base64.b64decode
    raw = AESGCM(_key(passphrase, u(blob["salt"]), blob.get("iter", ITER))).decrypt(u(blob["iv"]), u(blob["ct"]), None)
    if blob.get("gzip"):
        raw = gzip.decompress(raw)
    return json.loads(raw)


def write(path, obj, passphrase):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(encrypt(obj, passphrase), f)


def read(path, passphrase, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return decrypt(json.load(f), passphrase)
