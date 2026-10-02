"""
Issue a server certificate for awg-exporter signed by a local CA.

Certificates are generated on the machine running pyinfra (the CA key never
leaves it) and cached, so repeated deploys reuse them instead of restarting
the service with a fresh certificate every time.
"""

import datetime
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed448, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# Reissue the certificate when it expires sooner than this
RENEW_BEFORE = datetime.timedelta(days=30)


def _general_names(names):
    out = []
    for name in names:
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            out.append(x509.DNSName(name))
    return out


def _load_ca(cert_path, key_path, password):
    ca_cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    ca_key = serialization.load_pem_private_key(
        Path(key_path).read_bytes(),
        password=password.encode() if password else None,
    )
    if ca_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) != ca_cert.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ):
        raise ValueError(f"CA key {key_path} does not match CA certificate {cert_path}")
    return ca_cert, ca_key


def _is_reusable(cert_path, key_path, ca_cert, sans):
    if not (cert_path.is_file() and key_path.is_file()):
        return False
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        cert.verify_directly_issued_by(ca_cert)
    except Exception:
        return False

    now = datetime.datetime.now(datetime.timezone.utc)
    cert_sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return (
        set(cert_sans) == set(sans)
        and cert.not_valid_after_utc - now > RENEW_BEFORE
        and cert.public_key() == key.public_key()
    )


def ensure_cert(out_dir, ca_cert_path, ca_key_path, ca_key_password, names, days):
    """
    Return (cert_path, key_path) of a certificate for `names` signed by the CA,
    reusing the one cached in `out_dir` while it is still valid.
    """
    out_dir = Path(out_dir)
    cert_path = out_dir / "cert.pem"
    key_path = out_dir / "key.pem"

    ca_cert, ca_key = _load_ca(ca_cert_path, ca_key_path, ca_key_password)
    sans = _general_names(names)

    if _is_reusable(cert_path, key_path, ca_cert, sans):
        return cert_path, key_path

    now = datetime.datetime.now(datetime.timezone.utc)
    not_after = min(now + datetime.timedelta(days=days), ca_cert.not_valid_after_utc)

    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, None if isinstance(ca_key, (ed25519.Ed25519PrivateKey, ed448.Ed448PrivateKey)) else hashes.SHA256())
    )

    chain = cert.public_bytes(serialization.Encoding.PEM)
    # An intermediate CA has to be sent along with the leaf for clients to build the chain
    if ca_cert.issuer != ca_cert.subject:
        chain += ca_cert.public_bytes(serialization.Encoding.PEM)

    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    cert_path.write_bytes(chain)

    return cert_path, key_path
