from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
comp=(ROOT/'linkvideo_vpn_helper/ui/components.py').read_text(encoding='utf-8')
auto=(ROOT/'linkvideo_vpn_helper/services/vpn_automation_service.py').read_text(encoding='utf-8')
theme=(ROOT/'linkvideo_vpn_helper/theme.py').read_text(encoding='utf-8')
assert 'class BusyDialog(QDialog):' in comp
assert 'self.hide()' in comp and 'dlg.show_centered()' in comp
assert '_set_menu_enabled' in auto and 'RouterOS не подтвердил запуск LV Scheduler-задач' in auto
assert 'activity_run_count' in auto and 'restore_run_count' in auto
for name in ('Розовое молочко','Светлая LinkVideo','Лавандовая','Тёмно-синяя','Полуночная','Тёмная вишня','Графитовая'):
    assert name in theme
for removed in ('Глубокий океан','Ночная сова','Светлая LinkVideo", "#F2F6FA'):
    assert removed not in theme
print('CORE TESTS 3.0.5 VPN RUNTIME/THEME/OVERLAY OK')
