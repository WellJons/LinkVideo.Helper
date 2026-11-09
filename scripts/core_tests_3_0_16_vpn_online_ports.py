from __future__ import annotations

"""Offline safety checks: observed PPP peer IP and online-account port estimate.

The estimate is deliberately NOT a TCP/RTSP probe. A caller-id is the source
address observed by the router, not the VPN-assigned profile address.
"""

from pathlib import Path
from unittest.mock import patch

from linkvideo_vpn_helper.services.vpn_service import (
    SessionCredentials, VPNService, count_online_account_ports, _observed_tunnel_ip,
)

def nat(port, remote, comment, *, rid, disabled="false", protocol="tcp", action="dst-nat"):
    return {".id":rid,"chain":"dstnat","action":action,"protocol":protocol,
            "disabled":disabled,"dst-port":str(port),"to-addresses":remote,
            "comment":comment}


active=[
    {"name":"alice","address":"172.16.1.10","service":"l2tp","caller-id":"203.0.113.23"},
    {"name":"bob","address":"172.16.1.11","service":"l2tp","caller-id":"198.51.100.7"},
]
rules=[
    nat(60001,"172.16.1.10","alice",rid="*1"),     # linked by account
    nat(60002,"172.16.1.10","old note",rid="*2"), # linked by unique PPP IP
    nat(60003,"172.16.1.31","gone",rid="*3"),     # disconnected
    nat(60004,"172.16.1.11","bob",rid="*4",disabled="yes"),
    nat(60005,"172.16.1.10","alice",rid="*5"),
    nat(60005,"172.16.1.11","bob",rid="*6"),      # conflicting same port
    nat(60006,"172.16.1.11","bob",rid="*7"),      # another online client
    nat(60007,"172.16.1.10","alice",rid="*8",protocol="udp"),
    nat("60008-60009","172.16.1.10","alice",rid="*9"),
    nat(60010,"172.16.1.12","alice",rid="*10"),   # stale wrong internal IP
    nat(60011,"172.16.1.11","bob",rid="*11",action="drop"),
]
assert count_online_account_ports(active,rules)==3, "Only unique enabled online TCP ports"
assert count_online_account_ports([],rules)==0, "Inactive accounts cannot produce online ports"
assert count_online_account_ports(active[:1],rules)==2, "Only first client is online"
assert count_online_account_ports(
    [{"name":"alice","address":"172.16.1.10","service":"pptp"}],rules)==0, "Only L2TP"

assert _observed_tunnel_ip("203.0.113.23")=="203.0.113.23"
assert _observed_tunnel_ip("2001:db8::1")=="2001:db8::1"
assert _observed_tunnel_ip("555-1212")==""  # Caller-ID may be a phone number
assert _observed_tunnel_ip("192.168.1.40")=="192.168.1.40" # CGNAT/private is still observed

class FakeRouter:
    routes=[]
    def __init__(self,*args,**kwargs):
        self.args=args
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def print(self,path,params=None):
        FakeRouter.routes.append((path,params))
        return {
            "/system/resource":[{"cpu-load":"27","total-memory":"1000","free-memory":"750"}],
            "/ppp/secret":[{"name":"alice"},{"name":"bob"}],
            "/ppp/active":active,
            "/ip/firewall/nat":rules,
        }[path]

with patch("linkvideo_vpn_helper.services.vpn_service.RouterOSAPIClient",FakeRouter):
    report=VPNService().analyze_server_quick("example.invalid",SessionCredentials("staff","secret"))
assert report.clients_online==2
assert report.ports_active==3
assert report.ports_total>=report.ports_active
assert report.cpu_load==27
assert "/ip/firewall/connection" not in [p for p,_ in FakeRouter.routes]
assert "/interface" not in [p for p,_ in FakeRouter.routes]

vpn=VPNService()
snapshot={
    "secrets":[{"name":"alice","password":"client-pass","profile":"alice","disabled":"false"}],
    "profiles":[{"name":"alice","remote-address":"172.16.1.10"}],
    "actives":[{"name":"alice","address":"172.16.1.10","caller-id":"203.0.113.23","uptime":"2h"}],
    "nat_rules":[nat(60001,"172.16.1.10","alice",rid="*1")],
    "connections":[],"interfaces":[],"traffic_monitors":{},
}
client=vpn._build_client_records("example.invalid",snapshot)[0]
assert client.is_online
assert client.tunnel_source_ip=="203.0.113.23"
assert client.remote_address=="172.16.1.10"  # Assigned VPN address is different
snapshot["actives"]=[]
assert vpn._build_client_records("example.invalid",snapshot)[0].tunnel_source_ip==""
snapshot["actives"]=[{"name":"alice","caller-id":"not-an-ip"}]
assert vpn._build_client_records("example.invalid",snapshot)[0].tunnel_source_ip==""

root=Path(__file__).resolve().parents[1]
client_ui=(root/"linkvideo_vpn_helper/ui/pages/search_manage_page.py").read_text(encoding="utf-8")
server_ui=(root/"linkvideo_vpn_helper/ui/pages/vpn_servers_page.py").read_text(encoding="utf-8")
assert '("IP источника туннеля", c.tunnel_source_ip' in client_ui
assert "password_view.setEchoMode(QLineEdit.EchoMode.Password)" not in client_ui
assert 'password_view.setEchoMode(QLineEdit.EchoMode.Normal)' in client_ui
assert '"Порты онлайн / всего"' in server_ui
assert "self.m_online_ports.setValue(str(online_ports_total))" in server_ui
print("CORE TESTS 3.0.16 DESKTOP VPN ONLINE PORTS / CALLER-IP OK")
