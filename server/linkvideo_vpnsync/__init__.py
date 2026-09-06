"""LinkVideo.VPNSync server package."""

from .db_routeros_address_compat import install_routeros_address_compat
from .monitor_runtime_compat import install_monitor_runtime_compat

install_routeros_address_compat()
install_monitor_runtime_compat()
