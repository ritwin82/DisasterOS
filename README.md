# DisasterOS 2.0

DisasterOS is a cross-platform Python resilience layer. It monitors the host, identifies unusual metric combinations, estimates resource risk, chooses allowlisted recovery actions, verifies outcomes, and records an audit trail. It is **not** a custom operating system or a guarantee against failures.

## Windows setup

From PowerShell in `C:\Users\ritwi\Documents\DisasterOS`:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Open `http://127.0.0.1:8501`. The included `.streamlit/config.toml` binds to localhost. If Streamlit asks for an onboarding email on first use, leave it blank and press Enter. Run tests with `python -m pytest -q`.

## Ubuntu on WSL2 setup

If Ubuntu is not installed, use `wsl --install -d Ubuntu` in an elevated PowerShell terminal, complete the Ubuntu first-run setup, then reopen Ubuntu. Check `wsl --version` in PowerShell. In the Ubuntu shell:

```bash
cd /mnt/c/Users/ritwi/Documents/DisasterOS
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 -m venv ~/.venvs/disasteros
source ~/.venvs/disasteros/bin/activate
pip install -r requirements.txt
DISASTEROS_BACKEND=linux streamlit run app.py
```

This runs Linux monitoring and the managed-child simulations without cgroup permissions. The **Platform** tab reports what is actually available. `DISASTEROS_BACKEND=windows` on Linux, or `DISASTEROS_BACKEND=linux` on Windows, fails explicitly rather than controlling the wrong host.

### Optional cgroups v2 resource control

The Linux backend applies CPU and memory limits only to the application-owned `stress_worker`, inside a dedicated delegated cgroup v2 subtree. It never writes to arbitrary cgroup paths. If user systemd and controller delegation are available, launch this way from the Ubuntu project directory with the virtual environment active:

```bash
systemd-run --user --scope -p Delegate=yes -- "$(command -v python)" linux_cgroup_launcher.py
```

The launcher checks its delegated scope, moves only itself into a manager child, enables delegated `cpu` and `memory` controllers, and starts Streamlit without root. It sets `DISASTEROS_CGROUP_ROOT` for the backend. Start a CPU or memory scenario, then inspect the **Platform** tab to see the managed worker's measured CPU/RAM and to apply a bounded CPU quota (10–100% of one core) or memory limit (128–1024 MB). The backend reads the cgroup file back and checks workload health before reporting success. If delegation or permissions are missing, it reports `UNSUPPORTED` or `PERMISSION_DENIED`; ordinary monitoring and simulations still work. **Do not run Streamlit as root.**

### Optional user-systemd test service

Systemd must be active in Ubuntu. On WSL versions where it is not, add the following to `/etc/wsl.conf` and run `wsl --shutdown` from PowerShell before reopening Ubuntu:

```ini
[boot]
systemd=true
```

For a dedicated test unit, create `~/.config/systemd/user/disasteros-test.service` in Ubuntu. Replace the paths in this example with your project and Linux virtual environment paths:

```ini
[Unit]
Description=DisasterOS dedicated test service
[Service]
ExecStart=/home/YOUR_USER/.venvs/disasteros/bin/python /mnt/c/Users/ritwi/Documents/DisasterOS/workload.py service
Restart=no
[Install]
WantedBy=default.target
```

Then run:

```bash
systemctl --user daemon-reload
systemctl --user start disasteros-test.service
export DISASTEROS_SYSTEMD_SERVICE=disasteros-test.service
streamlit run app.py
```

Only an explicitly configured `disasteros-*.service` **user unit** can be restarted. The backend checks that the unit is loaded, uses `systemctl --user`, verifies it becomes active, applies a 30-second cooldown and at most two restart attempts. An optional `DISASTEROS_SERVICE_HEALTH_URL` must be a loopback HTTP URL; when set, a successful HTTP response is also required for verified recovery. The backend reports systemd as unavailable when it is not active or the unit is missing. Do not configure critical system units.

## Demonstration

### Both platforms

1. Leave the **System Overview** open for about 40 seconds. Isolation Forest needs 20 real baseline observations; simulated readings never train it.
2. Select **Service crash**. The app stops only its own demo service, decides to restart it, verifies its process is running, and records the result.
3. Select **CPU overload** or **Memory pressure**. A child process runs for at most 25 seconds with at most 64 MB allocated. The dashboard clearly labels injected pressure fields. Windows stops and verifies the owned worker; Linux applies and verifies a CPU or memory limit when cgroup delegation is available, otherwise it stops the worker.
4. Select **Network failure**. Only the test dependency becomes unavailable; recovery enables simulated offline mode. The host network stays connected.
5. Select **Disk pressure**. The policy records a storage recommendation as `UNVERIFIED` and deletes nothing.
6. Use **Stop simulation** to clean up the active worker.
7. Open **Benchmarks** and run the service-crash comparison. It performs measured recovery-disabled and recovery-enabled trials, persists both, and charts observed availability. No results are prefilled.

### Linux extras

The **Platform** tab shows cgroup and user-systemd capabilities. With a delegated cgroup scope, apply CPU and memory limits to the managed test worker and inspect the verified configuration. With the optional user service configured, stopping `disasteros-test.service` lets the policy attempt a bounded restart.

## Design and limits

- `monitoring.py`, `feature_engineering.py`, `anomaly_detector.py`, `risk_engine.py`: shared measurements and intelligence. Network throughput uses counter differences over elapsed time. Risk categories are transparent rule-based estimates; Isolation Forest detects unusual behavior, not validated failure probabilities.
- `platform_backends/`: typed action results, owned workload inspection, host-matched backend selection, Windows recovery, and Linux cgroup/systemd operations.
- `recovery_engine.py`: allowlisted policy decisions, dry-run mode, cooldowns, bounded retries, post-action checks, and audit events.
- `disaster_simulator.py`, `workload.py`: controlled scenarios and application-owned child processes.
- `database.py`: backward-compatible SQLite migration. Existing `metrics` and `events` records are retained; new source labels and benchmark records are added.
- `benchmarking.py`: measured service-crash trials. Detection and recovery timestamps are recorded from the running monitor. Availability is the fraction of observed samples with the managed service running during each trial.
- `app.py`: Streamlit presentation and background monitoring; the dashboard stays responsive during benchmarks.

The SQLite file is `disasteros.db`. Host metrics are real; injected scenario fields are identified individually in the dashboard and database. The system never terminates arbitrary host processes, disconnects host networking, deletes user files, or executes model-generated commands. Linux cgroup/systemd support depends on the actual WSL environment and permissions; a capability report is authoritative. The benchmark covers the managed demo-service crash scenario only and does not establish general resilience improvement.

## Troubleshooting

- **Virtual environment activation blocked in PowerShell:** run `Set-ExecutionPolicy -Scope Process Bypass`, then activate again, or call `.\.venv\Scripts\python.exe -m streamlit run app.py` directly.
- **Missing Python or packages:** use Python 3.11+ and install from `requirements.txt` inside the platform's own virtual environment. Do not reuse the Windows `.venv` inside Ubuntu.
- **Port in use:** `streamlit run app.py --server.port 8502`.
- **WSL/systemd unavailable:** check `wsl --version`, `/run/systemd/system`, and `systemctl --user status`. Plain Linux monitoring works without systemd.
- **Cgroup permission denied:** run the documented delegated-scope launcher as your normal user. If the WSL/systemd environment does not delegate both controllers, the platform reports the feature unavailable. Never elevate the whole dashboard.
- **Database locked:** close duplicate DisasterOS instances and restart the dashboard; the data file is preserved.

References: [Microsoft WSL systemd instructions](https://learn.microsoft.com/windows/wsl/systemd), [systemd cgroup delegation](https://systemd.io/CGROUP_DELEGATION/), [Linux cgroup v2 interface](https://docs.kernel.org/admin-guide/cgroup-v2.html).
