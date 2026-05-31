import logging
import os
import time

from flask import Flask, jsonify, make_response, render_template

import auth
import monitoring
import pi_sensors
import sensors
from config_api import config_bp
from monitoring import monitoring_bp

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
auth.init_auth(app, os.environ.get("HOSTERY_AUTH"))
app.register_blueprint(monitoring_bp)
app.register_blueprint(config_bp)

_sensors = sensors.Sensors()
_PI = sensors.has_pi_module()
_CACHE_BUST = int(time.time())


@app.route("/")
def index():
    # The document embeds inline CSS and references versioned JS, so it must
    # never be served from a stale cache (that hid earlier style fixes).
    resp = make_response(render_template("index.html", cache_bust=_CACHE_BUST))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/stats")
def stats():
    d = _sensors.get_stats()
    d["pi_module"] = _PI
    if _PI:
        d["throttle"] = pi_sensors.throttle_snapshot()
    return jsonify(d)


@app.route("/api/power")
def power():
    if not _PI:
        return jsonify({"timeline": [], "recent": [], "total_all_time": 0})
    return jsonify(pi_sensors.power_timeline())


if __name__ == "__main__":
    monitoring.init_db()
    if _PI:
        pi_sensors.set_temp_provider(_sensors.cpu_temp)
        pi_sensors.init_power_db()
        pi_sensors.start_throttle_monitor()
    monitoring.start_scheduler()
    host = os.environ.get("HOSTERY_BIND", "127.0.0.1")
    app.run(host=host, port=int(os.environ.get("HOSTERY_PORT", 5000)), debug=False)
