"""The fan reading is RPM (psutil.sensors_fans), so the Dashboard labels it
rpm: shown with "%", the Pi 5 fan read "9730%"."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = (ROOT / "static" / "js" / "dashboard.js").read_text()
HTML = (ROOT / "templates" / "index.html").read_text()


def test_fan_is_labelled_rpm():
    assert 'd.fan_speed + " rpm"' in JS
    assert 'd.fan_speed + "%"' not in JS
    assert re.search(r'id="fan-speed".*?>\s*--\s*rpm\s*</span', HTML, re.S)
