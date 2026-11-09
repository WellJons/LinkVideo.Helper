from __future__ import annotations

"""No-network desktop regressions for B2O port lookup, templates, and UI polish."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from zipfile import ZipFile
import sqlite3

from linkvideo_vpn_helper.services.camera_lookup import CameraChoice
from linkvideo_vpn_helper.services.vpn_camera_port_match import (
    account_login, explicit_camera_ports, match_vpn_ports, _detail_for_camera,
)
from linkvideo_vpn_helper.services.support_templates import TemplateStore


assert account_login("89950639067_1") == "89950639067"
assert account_login("89950639067_1_3") == "89950639067"
assert account_login("test_customer") == "test_customer"
assert account_login("customer_0") == "customer_0"
assert explicit_camera_ports({"rtsp_url": "rtsp://admin:secret@host.example:11690/live"}) == {11690}
assert explicit_camera_ports({"rtsp_url": "rtsp://host.example/live"}) == set()
assert explicit_camera_ports({"network":{"external_rtsp_port":"11691"}}) == {11691}
assert explicit_camera_ports({"port":"maybe 11690", "rtsp_url":"http://host:11690/"}) == set()
assert explicit_camera_ports({"camera_stream_main":
    "rtsp://admin:super-secret@vpn02.linkvideo.ru:11690/live"}) == {11690}
assert explicit_camera_ports({"camera_stream_main":
    "rtsp://vpn02.linkvideo.ru/live"}) == set()  # Never infer default port

class FakeB2O:
    def token(self):
        return "private-token"

b2o = FakeB2O()
creds = SimpleNamespace(username="operator", password="private")
first = CameraChoice("999","241" if False else 241,"Россия","Камера вход",
                     customer_login="89950639067")
second = CameraChoice("1000",241,"Россия","Камера склад",
                      customer_login="89950639067")
other = CameraChoice("2000",241,"Россия","Чужая камера",
                     customer_login="somebodyelse")
kz = CameraChoice("3000",1721,"Казахстан","Другая страна",
                  customer_login="89950639067")
choices = (first,second,other,kz)

def exact_lookup(login, b2o, creds):
    assert login == "89950639067"
    return choices

def detail(b2o, camera, login):
    return camera, {"999":{11690}, "1000":{11691}, "2000":{11690},
                    "3000":{11690}}[camera.camera_id]

report = match_vpn_ports("89950639067_1", [11690,11691,11692],b2o,creds,
                         country="Россия", lookup=exact_lookup, detail_reader=detail)
assert {(x.port,x.camera_id) for x in report.links} == {(11690,"999"),(11691,"1000")}
assert report.unmatched == (11692,)
assert report.checked_cameras == 2
assert not report.incomplete
report = match_vpn_ports("89950639067_3",[11690],b2o,creds,country="Россия",
    lookup=lambda login,*_: (first,second),
    detail_reader=lambda _b, choice, login: (choice,{11690}))
assert report.ambiguous == (11690,) and not report.links

# If B2O failed to return complete camera details, do NOT claim that the
# remaining single match is definitely unique.
def incomplete(_b,choice,_l):
    if choice.camera_id == "1000":
        raise TimeoutError("B2O unavailable")
    return choice,{11690}
report = match_vpn_ports("89950639067_3",[11690],b2o,creds,country="Россия",
    lookup=lambda *_: (first,second),detail_reader=incomplete)
assert report.incomplete and not report.links
assert match_vpn_ports("client_3",[],b2o,creds).links == ()

# Regression: B2O list and camera details can find FIVE cameras but have NO
# RTSP ports. The actual external ports live in /camera-streams-urls.
class StreamB2O:
    CAMERA_URL = "https://api.b2o.goodline.info/ords/mobile/vc2/operators/{operator}/cameras/{camera}"
    def __init__(self):
        self.routes = []
        self.fail_stream = False
    def token(self):
        return "session-token"
    def normalize_camera_id(self,camera_id,operator_id):
        return str(camera_id), str(camera_id)
    def request_json(self,url,token,timeout=12):
        assert token == "session-token"
        self.routes.append(url)
        camera_id = url.split("/cameras/")[1].split("/")[0]
        if url.endswith("/camera-streams-urls"):
            if self.fail_stream:
                raise TimeoutError("B2O stream endpoint unavailable")
            return {"data":{"camera_stream_main": "rtsp://operator:secret@vpn02.linkvideo.ru:" +
                    {"999":"11690","1000":"11691","1001":"11692"}.get(camera_id,"11693") + "/live"}}
        return {"data":{"id_camera":camera_id,"account_login":"89950639067",
                        "camera_status":"online"}}

actual_b2o=StreamB2O()
five = tuple(CameraChoice(str(999+i),241,"Россия",f"Камера {i}",
                          customer_login="89950639067") for i in range(5))
actual = match_vpn_ports("89950639067_3",[11690,11691,11692],actual_b2o,creds,
                         country="Россия", lookup=lambda *_: five)
assert {(x.port,x.camera_id) for x in actual.links} == {
    (11690,"999"),(11691,"1000"),(11692,"1001")}
assert actual.stream_cameras == 5
assert actual.checked_cameras == 5
assert len([url for url in actual_b2o.routes if url.endswith("/camera-streams-urls")]) == 5
assert not actual.incomplete

actual_b2o.fail_stream=True
partial = match_vpn_ports("89950639067_3",[11690],actual_b2o,creds,
                          country="Россия", lookup=lambda *_: five)
assert partial.incomplete and not partial.links and partial.stream_errors == 5

# If camera detail reports a conflicting owner, do not fetch or link it.
wrong=StreamB2O()
def conflicting(url, token, timeout=12):
    wrong.routes.append(url)
    return {"data":{"id_camera":"999", "account_login":"another_customer"}}
wrong.request_json=conflicting
assert _detail_for_camera(wrong,first,"89950639067")[1] == set()
assert not any(u.endswith("/camera-streams-urls") for u in wrong.routes)


with TemporaryDirectory() as temp:
    root=Path(temp)
    store=TemplateStore(root)
    assert "Диагностика" in store.categories()
    ident=store.save("Разрыв Wi-Fi","Здравствуйте!\nПроверьте роутер.","Диагностика")
    assert ident > 0
    assert len(store.list("wi-fi")) == 1
    assert len(store.list("проверьте")) == 1
    assert len(store.list(category="Диагностика")) == 1
    assert not store.list(favorites=True)
    store.favorite(ident,True)
    assert store.list(favorites=True)[0].id == ident
    store.save("Wi-Fi — уточнение","Смена канала","Диагностика",ident)
    assert store.get(ident).body == "Смена канала"
    png=root/"sample.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"example-png")
    store.attach_image(ident,png)
    images=store.images(ident)
    assert len(images)==1 and images[0][1].is_file()
    store.detach_image(images[0][0],ident)
    assert not store.images(ident)
    doc=root/"reply.docx"
    with ZipFile(doc,"w") as archive:
        archive.writestr("word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Здравствуйте!</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Проверили архив.</w:t></w:r></w:p></w:body></w:document>')
    imported=store.import_docx(doc,"Видеоархив")
    assert store.get(imported).body=="Здравствуйте!\nПроверили архив."
    # Data must still be present when the page/store reopens.
    reopened=TemplateStore(root)
    assert reopened.get(ident).favorite
    assert reopened.get(imported).category=="Видеоархив"
    reopened.delete(ident)
    assert reopened.get(ident) is None

project=Path(__file__).resolve().parents[1]
search_ui=(project/"linkvideo_vpn_helper/ui/pages/search_manage_page.py").read_text(encoding="utf-8")
main_ui=(project/"linkvideo_vpn_helper/ui/main_window.py").read_text(encoding="utf-8")
assert 'password_value = QLabel(c.password)' in search_ui
assert 'password_view = QLineEdit(c.password)' not in search_ui
assert 'info_l.addLayout(info_nav)' not in search_ui
assert '("Камеры B2O по портам")' not in search_ui
assert 'ID камеры B2O: ' in search_ui
assert "pl.addLayout(camera_lookup)" in search_ui
assert "pl.addWidget(camera_status)" in search_ui
assert 'Трафик порта:' in search_ui
assert "self._start_camera_port_check()" in search_ui
assert '"templates", "▤", "Шаблоны ответов"' in main_ui
assert 'SupportTemplatesPage(self)' in main_ui

print("CORE TESTS DESKTOP B2O CAMERA MATCH + SUPPORT TEMPLATES OK")
