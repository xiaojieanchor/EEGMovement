"""Private application CA; never install into global Windows/Android trust."""
import datetime,ipaddress,os,ssl
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID

def create_identity(root,address):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    if (root/'ca.pem').exists():return
    now=datetime.datetime.now(datetime.timezone.utc)
    ca_key=rsa.generate_private_key(65537,3072);server_key=rsa.generate_private_key(65537,3072)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'EEGMovement Relay application CA')])
    ca=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-datetime.timedelta(days=1)).not_valid_after(now+datetime.timedelta(days=1825))
        .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True,key_encipherment=False,content_commitment=False,data_encipherment=False,key_agreement=False,key_cert_sign=True,crl_sign=True,encipher_only=None,decipher_only=None),critical=True).sign(ca_key,hashes.SHA256()))
    server=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,address)])).issuer_name(name).public_key(server_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1)).not_valid_after(now+datetime.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(address))]),critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),critical=False).sign(ca_key,hashes.SHA256()))
    for filename,key in [('ca.key',ca_key),('server.key',server_key)]:
        (root/filename).write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));os.chmod(root/filename,0o600)
    (root/'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM));(root/'server.pem').write_bytes(server.public_bytes(serialization.Encoding.PEM))

def client_context(ca):
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);context.minimum_version=ssl.TLSVersion.TLSv1_2
    if isinstance(ca,str) and 'BEGIN CERTIFICATE' in ca:context.load_verify_locations(cadata=ca)
    else:context.load_verify_locations(cafile=str(ca))
    context.check_hostname=True;context.verify_mode=ssl.CERT_REQUIRED
    return context
