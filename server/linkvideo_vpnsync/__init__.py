"""LinkVideo.VPNSync server package."""

from .db_routeros_address_compat import install_routeros_address_compat
from .db_nat_rule_compat import install_nat_rule_compat
from .timezone_compat import install_business_timezone
from .monitor_runtime_compat import install_monitor_runtime_compat
from .activity_compat import install_activity_tracking

install_routeros_address_compat()
install_nat_rule_compat()
install_business_timezone()
install_monitor_runtime_compat()
install_activity_tracking()
