"""Entry point of the packaged Snapcut server."""
import os
import sys

import certifi

# The bundled OpenSSL looks for certificates where the build machine's Python
# keeps them (on the macOS runner, inside its Python.framework), which doesn't
# exist on the user's machine: use the CA bundle that ships with the app.
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from snapcut.server import main  # noqa: E402

sys.exit(main())
