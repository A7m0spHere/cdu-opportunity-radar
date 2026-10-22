"""Project-owned verified CA context, independent of stale SSL_CERT_FILE."""
import re
import ssl

import certifi

MINIMUM_CERTIFI = (2026, 10, 19)


def trusted_context():
    match=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)',certifi.__version__)
    if not match or tuple(map(int,match.groups()))<MINIMUM_CERTIFI:
        raise ValueError('certifi 2026.10.19 or later is required')
    # An explicit cafile avoids loading an outdated process-level override.
    # Proxy handling stays with HTTPX; certificate/hostname checks stay enabled.
    context=ssl.create_default_context(cafile=certifi.where())
    if context.verify_mode!=ssl.CERT_REQUIRED or not context.check_hostname:
        raise ValueError('Certificate and hostname verification are required')
    return context
