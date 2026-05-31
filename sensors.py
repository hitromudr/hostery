import shutil
import time

import psutil


def has_pi_module():
    """True when the optional Raspberry Pi sensor module can run here."""
    return shutil.which("vcgencmd") is not None


class Sensors:
    """Cross-platform host metrics. Every reader degrades to 0/None, never raises."""

    def __init__(self):
        self._io = {"last_disk": None, "last_net": None, "last_time": 0.0}

    def cpu_temp(self):
        try:
            temps = psutil.sensors_temperatures()
        except Exception:
            return 0.0
        if not temps:
            return 0.0
        for pref in ("coretemp", "cpu_thermal", "k10regs", "k10temp", "acpitz"):
            if pref in temps and temps[pref]:
                return float(temps[pref][0].current or 0.0)
        first = next(iter(temps.values()))
        return float(first[0].current or 0.0) if first else 0.0

    def fan_speed(self):
        try:
            fans = psutil.sensors_fans()
        except Exception:
            return 0
        if not fans:
            return 0
        first = next(iter(fans.values()))
        return int(first[0].current or 0) if first else 0

    def cpu_freq(self):
        try:
            f = psutil.cpu_freq()
        except Exception:
            f = None
        if not f:
            return 0, 0, 0
        return int(f.current or 0), int(f.min or 0), int(f.max or 0)

    def load_avg(self):
        try:
            return [round(x, 2) for x in psutil.getloadavg()]
        except Exception:
            return [0.0, 0.0, 0.0]

    def io_rates(self):
        now = time.time()
        disk_curr = psutil.disk_io_counters()
        net_curr = psutil.net_io_counters()
        stats = {"disk_read_mb_s": 0.0, "disk_write_mb_s": 0.0,
                 "net_rx_mb_s": 0.0, "net_tx_mb_s": 0.0}
        last_t = self._io["last_time"]
        if last_t > 0:
            dt = now - last_t
            if dt > 0:
                if self._io["last_disk"] and disk_curr:
                    stats["disk_read_mb_s"] = round(
                        (disk_curr.read_bytes - self._io["last_disk"].read_bytes) / 1048576 / dt, 2)
                    stats["disk_write_mb_s"] = round(
                        (disk_curr.write_bytes - self._io["last_disk"].write_bytes) / 1048576 / dt, 2)
                if self._io["last_net"] and net_curr:
                    stats["net_rx_mb_s"] = round(
                        (net_curr.bytes_recv - self._io["last_net"].bytes_recv) / 1048576 / dt, 2)
                    stats["net_tx_mb_s"] = round(
                        (net_curr.bytes_sent - self._io["last_net"].bytes_sent) / 1048576 / dt, 2)
        self._io["last_disk"] = disk_curr
        self._io["last_net"] = net_curr
        self._io["last_time"] = now
        return stats

    def get_stats(self):
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        freq, fmin, fmax = self.cpu_freq()
        return {
            "cpu_temp": self.cpu_temp(),
            "cpu_freq_mhz": freq,
            "cpu_freq_min": fmin,
            "cpu_freq_max": fmax,
            "fan_speed": self.fan_speed(),
            "load_avg": self.load_avg(),
            "ram_percent": mem.percent,
            "ram_used_gb": round(mem.used / (1024 ** 3), 1),
            "ram_total_gb": round(mem.total / (1024 ** 3), 1),
            "cpu_per_core": psutil.cpu_percent(percpu=True),
            "io": self.io_rates(),
            "disk_percent": disk.percent,
            "disk_used_gb": round(disk.used / (1024 ** 3), 1),
            "disk_total_gb": round(disk.total / (1024 ** 3), 1),
        }
