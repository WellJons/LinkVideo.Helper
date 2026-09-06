"""LinkVideo.Helper UI package bootstrap."""

# Install the final 3.0.13 VPN compatibility layer before page instances are
# created. Later compatibility installers may wrap these methods, but the final
# layer owns the base VPN layout, archived-search completion and event-driven
# Sheets coordinator lifecycle.
from linkvideo_vpn_helper.ui.vpn_final_3_0_13_ux import install_vpn_final_3_0_13_ux

install_vpn_final_3_0_13_ux()
