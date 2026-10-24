"""Local operator credentials in the Windows vault, never in project files."""
import hashlib,json,sys


def _backend():
    if sys.platform!='win32':return None
    try:
        import win32cred
        return win32cred
    except ImportError:return None


def credential_target(profile):
    identity={k:profile.get(k) for k in ['host','port','username','hostkey_sha256']}
    return 'EEGMovement/gnautilus/'+hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()


def load_credential(profile):
    backend=_backend()
    if backend is None:return None
    try:
        row=backend.CredRead(credential_target(profile),backend.CRED_TYPE_GENERIC)
        value=row['CredentialBlob']
        return value.decode('utf-16-le') if isinstance(value,bytes) else value
    except Exception:return None


def save_credential(profile,password):
    backend=_backend()
    if backend is None:raise RuntimeError('Windows credential vault is unavailable')
    if not password or not profile.get('hostkey_sha256'):raise ValueError('Verified host identity and credential required')
    backend.CredWrite(dict(Type=backend.CRED_TYPE_GENERIC,TargetName=credential_target(profile),
        UserName=profile['username'],CredentialBlob=password,
        Persist=backend.CRED_PERSIST_LOCAL_MACHINE,Comment='EEGMovement private compute; pinned SSH identity'),0)


def forget_credential(profile):
    backend=_backend()
    if backend is None:return
    try:backend.CredDelete(credential_target(profile),backend.CRED_TYPE_GENERIC,0)
    except Exception:pass
