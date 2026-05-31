import config_api as c

def test_cockpit_status_running():
    assert c.parse_cockpit_status("active\n/usr/bin/cockpit-bridge") == "running"

def test_cockpit_status_installed_stopped():
    assert c.parse_cockpit_status("inactive\n/usr/bin/cockpit-bridge") == "installed-but-stopped"

def test_cockpit_status_absent():
    assert c.parse_cockpit_status("inactive\n") == "absent"

def test_install_command_apt():
    cmd = c.install_cockpit_command("apt-get")
    assert "apt-get install -y cockpit" in cmd
    assert "enable --now cockpit.socket" in cmd

def test_install_command_dnf():
    assert "dnf install -y cockpit" in c.install_cockpit_command("dnf")

def test_pkg_manager_probe_command_lists_managers():
    probe = c.pkg_manager_probe_command()
    for mgr in ("apt-get", "dnf", "zypper", "pacman"):
        assert mgr in probe
