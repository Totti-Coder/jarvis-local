"""Certificado HTTPS para poder usar el microfono desde el movil.

POR QUE HACE FALTA ESTO

El microfono del navegador (getUserMedia) solo existe en un "contexto
seguro": HTTPS, o localhost. Desde el movil entras por la IP de la wifi
—http://192.168.1.129:8000— y eso NO es contexto seguro, asi que la API
del microfono ni siquiera aparece. No es un permiso que puedas conceder:
el objeto no esta.

Con HTTPS si. Y como el certificado lo firma tu propio ordenador, el
navegador avisara la primera vez de que no lo conoce. Es normal: no hay
ninguna autoridad en el mundo que pueda certificar tu IP privada.

POR QUE NO SE USA UN TUNEL (ngrok y companyia)

Daria un certificado bueno sin avisos, pero el audio de tus
conversaciones pasaria por un tercero y haria falta internet. El
proyecto entero existe para que eso no pase.

Uso:
    python certificado.py          crea o renueva el certificado
    python certificado.py --ver    muestra para que IPs vale
"""

import datetime
import ipaddress
import socket
import sys
from pathlib import Path

AQUI = Path(__file__).parent
CLAVE = AQUI / "clave.pem"
CERT = AQUI / "cert.pem"

# Un ano. Al caducar se vuelve a ejecutar esto y listo.
DIAS = 365


def ip_local():
    """La IP de este equipo en la red local."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # No se conecta a nada: solo se pregunta al sistema por que
        # interfaz saldria el trafico, y de ahi sale la IP buena.
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def _nombres():
    """Para que direcciones vale el certificado.

    Se meten TODAS las IPs del equipo, no solo la principal: con wifi y
    cable a la vez hay varias, y un certificado que no incluya la que
    usas de verdad hace que el navegador lo rechace del todo.
    """
    ips = {"127.0.0.1"}
    actual = ip_local()
    if actual:
        ips.add(actual)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ips.add(info[4][0])
    except socket.gaierror:
        pass
    return sorted(ips), ["localhost", socket.gethostname().lower()]


def generar():
    """Crea clave y certificado autofirmados. Devuelve (ips, nombres)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    ips, nombres = _nombres()

    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeto = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "Jarvis"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Jarvis local"),
    ])

    # El SAN es obligatorio. Los navegadores modernos IGNORAN el Common
    # Name desde hace anos: sin SAN el certificado se rechaza sin darte
    # siquiera la opcion de aceptarlo.
    alternativos = [x509.DNSName(n) for n in nombres]
    alternativos += [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]

    ahora = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(sujeto)
        .issuer_name(sujeto)                    # autofirmado: emisor = sujeto
        .public_key(clave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - datetime.timedelta(days=1))
        .not_valid_after(ahora + datetime.timedelta(days=DIAS))
        .add_extension(x509.SubjectAlternativeName(alternativos), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None),
                       critical=True)
        .sign(clave, hashes.SHA256())
    )

    CLAVE.write_bytes(clave.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()))
    CERT.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    try:
        import os
        os.chmod(CLAVE, 0o600)     # es una clave privada
    except OSError:
        pass

    return ips, nombres


def existe():
    return CLAVE.exists() and CERT.exists()


def caduca_en():
    """Dias que le quedan al certificado. None si no hay o no se lee."""
    if not CERT.exists():
        return None
    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(CERT.read_bytes())
        fin = cert.not_valid_after_utc
        return (fin - datetime.datetime.now(datetime.timezone.utc)).days
    except Exception:
        return None


def vale_para():
    """IPs y nombres que cubre el certificado actual."""
    if not CERT.exists():
        return [], []
    try:
        from cryptography import x509
        cert = x509.load_pem_x509_certificate(CERT.read_bytes())
        san = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName).value
        return ([str(i) for i in san.get_values_for_type(x509.IPAddress)],
                san.get_values_for_type(x509.DNSName))
    except Exception:
        return [], []


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    if "--ver" in sys.argv:
        if not existe():
            print("No hay certificado. Ejecuta: python certificado.py")
            sys.exit(1)
        ips, nombres = vale_para()
        print(f"Certificado válido para:")
        for i in ips:
            print(f"    https://{i}:8000")
        for n in nombres:
            print(f"    https://{n}:8000")
        dias = caduca_en()
        print(f"\nCaduca en {dias} días." if dias is not None else "")
        sys.exit(0)

    print("Generando certificado para tu red local...")
    ips, nombres = generar()

    print("\nListo. Vale para:")
    for i in ips:
        print(f"    https://{i}:8000")

    actual = ip_local()
    print()
    print("=" * 64)
    print("DESDE EL MÓVIL")
    print("=" * 64)
    print("  1. Arranca con:   python servidor.py --red")
    if actual:
        print(f"  2. En el móvil:   https://{actual}:8000")
    print("  3. Saldrá un aviso de que la conexión no es privada.")
    print("     Es normal: el certificado lo ha firmado tu propio PC y")
    print("     no hay ninguna autoridad que pueda certificar una IP")
    print("     privada. Dale a 'Configuración avanzada' -> 'Continuar'.")
    print("  4. Acepta el permiso del micrófono cuando lo pida.")
    print()
    print("  Ojo: tiene que ser https://, no http://")
    print("=" * 64)
