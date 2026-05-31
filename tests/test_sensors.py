import sensors

def test_io_rates_first_call_returns_zeros():
    s = sensors.Sensors()
    rates = s.io_rates()
    assert rates["disk_read_mb_s"] == 0.0
    assert rates["net_rx_mb_s"] == 0.0

def test_get_stats_has_core_keys():
    s = sensors.Sensors()
    d = s.get_stats()
    for key in ("cpu_temp", "fan_speed", "ram_percent", "disk_percent",
                "cpu_per_core", "io", "load_avg", "cpu_freq_mhz"):
        assert key in d

def test_cpu_temp_returns_float_or_zero():
    s = sensors.Sensors()
    t = s.cpu_temp()
    assert isinstance(t, float)
    assert t >= 0.0

def test_has_pi_module_is_bool():
    assert isinstance(sensors.has_pi_module(), bool)
