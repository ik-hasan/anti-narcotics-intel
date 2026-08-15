"""Diagnose TLS failures when connecting to Aura.

A `self-signed certificate in certificate chain` error almost always means a proxy,
campus firewall or antivirus is intercepting TLS and presenting its own CA, which
Python's bundled certifi store does not trust. This prints who actually signed the
certificates we receive so the cause is unambiguous.

    python -m scripts.diagnose_tls
"""

import re
import socket
import ssl
import sys
from pathlib import Path

import certifi

TARGETS = [
    ("www.google.com", 443),
    ("console.neo4j.io", 443),
]


def aura_host() -> str | None:
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("NEO4J_URI="):
            uri = line.split("=", 1)[1].strip()
            return re.sub(r"^[a-z0-9+]+://", "", uri).split(":")[0] or None
    return None


def presented_issuer(host: str, port: int) -> str:
    """Fetch the chain without verifying, purely to report who signed it."""
    ctx = ssl._create_unverified_context()
    with socket.create_connection((host, port), timeout=10) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    try:
        from cryptography import x509

        return x509.load_der_x509_certificate(der).issuer.rfc4514_string()
    except Exception:  # noqa: BLE001
        return "(install cryptography to decode the issuer)"


def probe(host: str, port: int, label: str) -> bool:
    ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                issuer = dict(x[0] for x in tls.getpeercert()["issuer"])
                name = issuer.get("organizationName") or issuer.get("commonName")
                print(f"  {label:<22} OK    signed by: {name}")
                return True
    except ssl.SSLCertVerificationError as exc:
        print(f"  {label:<22} FAIL  {exc.verify_message}")
        try:
            print(f"  {'':<22}       presented issuer: {presented_issuer(host, port)}")
        except Exception as inner:  # noqa: BLE001
            print(f"  {'':<22}       could not read chain: {inner}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:<22} ERROR {type(exc).__name__}: {exc}")
        return False


def main() -> int:
    print(f"certifi bundle: {certifi.where()}\n")

    targets = list(TARGETS)
    host = aura_host()
    if host:
        masked = host[:4] + "****" + host[-24:] if len(host) > 30 else host
        targets.insert(0, (host, 7687))
        print(f"aura host: {masked}\n")

    print("Verifying against certifi (what the neo4j driver uses by default):")
    results = [probe(h, p, f"{h.split('.')[0]}:{p}") for h, p in targets]

    print("\nVerifying against the Windows certificate store (what Chrome uses):")
    try:
        import truststore

        for h, p in targets:
            ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            try:
                with socket.create_connection((h, p), timeout=10) as sock:
                    with ctx.wrap_socket(sock, server_hostname=h):
                        print(f"  {h.split('.')[0]+':'+str(p):<22} OK")
            except Exception as exc:  # noqa: BLE001
                print(f"  {h.split('.')[0]+':'+str(p):<22} FAIL  {exc}")
    except ImportError:
        print("  truststore not installed -- run: pip install truststore")

    if not all(results):
        print(
            "\nAt least one target failed certifi verification.\n"
            "If the presented issuer above is not a public CA (DigiCert, Google Trust\n"
            "Services, Let's Encrypt, Amazon), your traffic is being TLS-intercepted."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
