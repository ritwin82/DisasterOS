"""DisasterOS dashboard. Run with: streamlit run app.py"""
import atexit
import html
import threading
import time
from collections import deque
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from anomaly_detector import AnomalyDetector
from benchmarking import BenchmarkRunner
from config import HISTORY_LIMIT, SAMPLE_SECONDS, SCENARIOS
from database import Database
from disaster_simulator import DisasterSimulator
from platform_backends.factory import create_backend
from recovery_engine import RecoveryEngine
from risk_engine import RiskEngine


class DisasterOS:
    def __init__(self):
        self.db = Database()
        self.detector = AnomalyDetector()
        self.risk_engine = RiskEngine()
        self.simulator = DisasterSimulator(self.db)
        self.backend = create_backend(self.simulator)
        self.capabilities = self.backend.get_platform_capabilities()
        self.recovery = RecoveryEngine(self.db, self.backend)
        self.history = deque(maxlen=HISTORY_LIMIT)
        self.latest = {}
        self.error = None
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name="disasteros-monitor")
        self.thread.start()
        self.benchmarker = BenchmarkRunner(self)
        atexit.register(self.close)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                real = self.backend.collect_metrics()
                observed = self.simulator.overlay(real)
                observed["offline_mode"] = self.simulator.offline_mode
                if self.simulator.worker is None and hasattr(self.backend, "cleanup_managed_resources"):
                    self.backend.cleanup_managed_resources()
                observed["worker_alive"] = self.simulator.worker is not None and self.simulator.worker.poll() is None
                anomaly = self.detector.assess(real, simulated=observed["simulated"])
                risk = self.risk_engine.assess(observed, anomaly)
                systemd_info = (self.backend.inspect_workload("systemd_test_service")
                                if self.backend.name == "linux" and self.backend.systemd_service else None)
                systemd_alive = systemd_info.get("alive") if systemd_info else None
                decision = self.recovery.decide(observed, risk, self.simulator.service_alive(), systemd_alive, self.capabilities)
                decision_monotonic = time.monotonic() if decision["action"] != "none" else None
                decision_at = (datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                               if decision_monotonic is not None else None)
                action = self.recovery.execute(decision, observed) or self.recovery.last_action
                observed["anomaly_score"] = anomaly["score"]
                observed["is_anomaly"] = int(anomaly["is_anomaly"])
                observed["risk"] = risk["category"]
                observed["simulated"] = int(observed["simulated"])
                observed["measurement_source"] = "mixed" if observed["simulated_fields"] else "real"
                observed["backend"] = self.backend.name
                self.db.metric(observed)
                with self.lock:
                    self.history.append(observed)
                    self.latest = {"real": real, "observed": observed, "anomaly": anomaly,
                                   "risk": risk, "decision": decision, "action": action,
                                   "decision_at": decision_at, "decision_monotonic": decision_monotonic,
                                   "service_alive": self.simulator.service_alive(),
                                   "systemd_info": systemd_info,
                                   "offline_mode": self.simulator.offline_mode}
                    self.error = None
            except Exception as exc:
                with self.lock:
                    self.error = str(exc)
                try:
                    self.db.event("ERROR", "Monitoring loop error", result="FAILED", details=str(exc))
                except Exception:
                    pass
            self.stop_event.wait(SAMPLE_SECONDS)

    def start(self, scenario):
        with self.lock:
            self.recovery.reset_episode()
            self.simulator.start(scenario)

    def set_dry_run(self, enabled):
        with self.lock:
            self.recovery.dry_run = bool(enabled)

    def apply_policy(self, action, amount):
        with self.lock:
            if self.recovery.dry_run:
                raise RuntimeError("Disable dry-run mode before applying a resource limit")
            policy = {"action": action, "percent" if action == "cpu_quota" else "memory_mb": amount}
            outcome = self.backend.apply_resource_policy("stress_worker", policy)
            self.db.event("INFO" if outcome.succeeded else "WARNING", action,
                          self.simulator.scenario, outcome.verification,
                          details={"policy": policy, "execution": outcome.to_dict()})
            return outcome

    def stop(self):
        with self.lock:
            self.simulator.stop()
            if hasattr(self.backend, "cleanup_managed_resources"):
                self.backend.cleanup_managed_resources()
            self.recovery.reset_episode()

    def snapshot(self):
        with self.lock:
            return dict(self.latest), list(self.history), self.error

    def close(self):
        self.stop_event.set()
        self.simulator.close()
        if hasattr(self.backend, "cleanup_managed_resources"):
            self.backend.cleanup_managed_resources()
        if self.thread.is_alive() and threading.current_thread() is not self.thread:
            self.thread.join(timeout=3)


@st.cache_resource
def engine():
    return DisasterOS()


def action_label(value):
    return "No action" if not value or value == "none" else value.replace("_", " ").title()


def format_duration_ms(value):
    if value is None or pd.isna(value):
        return "—"
    value = float(value)
    return f"{value:,.0f} ms" if value < 1000 else f"{value / 1000:.2f} s"


def format_local_time(value):
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%d %b %Y · %H:%M:%S")
    except (TypeError, ValueError):
        return str(value)


def stage_panel(step, title, value, detail, tone="neutral"):
    safe = [html.escape(str(item)) for item in (step, title, value, detail)]
    return (f'<div class="recovery-stage {tone}"><div class="stage-step">{safe[0]}</div>'
            f'<div class="stage-title">{safe[1]}</div><div class="stage-value">{safe[2]}</div>'
            f'<div class="stage-detail">{safe[3]}</div></div>')


SCENARIO_HELP = {
    "CPU overload": "Raises CPU pressure; recovery limits or stops only the owned worker.",
    "Memory pressure": "Raises memory pressure; allocation remains capped at 64 MB.",
    "Disk pressure": "Injects a disk warning; recovery recommends action and deletes nothing.",
    "Service crash": "Stops the demo service so restart and health verification can be shown.",
    "Network failure": "Takes only the simulated dependency offline; host networking is unchanged.",
    "Combined disaster": "Combines resource and dependency pressure to exercise prioritization.",
}


st.set_page_config(page_title="DisasterOS | Resilience Console", layout="wide")
st.markdown("""
<style>
  :root {
    --canvas: #07101d; --surface: #101c2e; --surface-raised: #14233a;
    --line: #2a3c57; --text: #edf4ff; --muted: #a9bad1;
    --teal: #45d5c3; --blue: #79aaff; --amber: #f3bd67;
    --red: #ff7c86; --green: #69d6a0;
  }
  .stApp { background: var(--canvas); color: var(--text); }
  [data-testid="stHeader"] { background: rgba(7,16,29,.94); }
  .block-container { max-width: 1440px; padding-top: 2.2rem; padding-bottom: 4rem; }
  h1, h2, h3 { color: var(--text) !important; letter-spacing: -.025em; }
  h1 { font-size: clamp(2.25rem, 5vw, 4.25rem) !important; margin-bottom: .1rem !important; }
  h2 { margin-top: 1.1rem !important; }
  p, li { line-height: 1.55; }
  ::selection { background: #45d5c344; color: var(--text); }
  :focus-visible { outline: 3px solid var(--teal) !important; outline-offset: 3px !important; }
  .product-description { color: var(--muted); max-width: 72ch; font-size: 1.02rem; margin-bottom: 1.5rem; }
  .status-surface {
    display: grid; grid-template-columns: minmax(180px,.75fr) 2fr auto; gap: 1.5rem;
    align-items: center; background: var(--surface); border: 1px solid var(--line);
    border-radius: 14px; padding: 1.25rem 1.4rem; margin: .8rem 0 1.4rem;
  }
  .status-label, .stage-step { color: var(--muted); font-size: .75rem; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
  .status-value { font-size: 1.7rem; font-weight: 750; margin-top: .2rem; }
  .status-value.low { color: var(--green); } .status-value.medium { color: var(--amber); }
  .status-value.high, .status-value.critical { color: var(--red); }
  .status-context { color: var(--text); font-weight: 650; }
  .status-detail { color: var(--muted); margin-top: .2rem; }
  .source-tag { border: 1px solid var(--line); border-radius: 999px; padding: .45rem .75rem; color: var(--muted); white-space: nowrap; }
  div[data-testid="stMetric"] { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 1rem 1.1rem; }
  div[data-testid="stMetricLabel"] { color: var(--muted); }
  div[data-testid="stMetricValue"] { color: var(--text); font-variant-numeric: tabular-nums; }
  .recovery-flow { display: grid; grid-template-columns: repeat(3, 1fr); gap: .8rem; margin: .8rem 0 1.2rem; }
  .recovery-stage { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: 1rem; min-height: 150px; }
  .recovery-stage.success { border-color: #35765b; } .recovery-stage.warning { border-color: #7f6334; }
  .recovery-stage.danger { border-color: #82434b; }
  .stage-title { color: var(--muted); margin-top: .8rem; }
  .stage-value { color: var(--text); font-size: 1.2rem; font-weight: 700; margin-top: .2rem; }
  .stage-detail { color: var(--muted); margin-top: .55rem; font-size: .9rem; line-height: 1.4; }
  [data-baseweb="tab-list"] { gap: .35rem; overflow-x: auto; scrollbar-width: thin; }
  [data-baseweb="tab"] { min-height: 46px; white-space: nowrap; }
  .stButton button { border-radius: 9px; min-height: 42px; border-color: #3a5272; }
  .stButton button:hover { border-color: var(--teal); color: var(--text); }
  [data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
  hr { border-color: var(--line); }
  @media (max-width: 800px) {
    .block-container { padding: 1.25rem .85rem 3rem; }
    .status-surface { grid-template-columns: 1fr; gap: .8rem; }
    .source-tag { width: fit-content; }
    .recovery-flow { grid-template-columns: 1fr; }
  }
</style>
""", unsafe_allow_html=True)

system = engine()
st.title("DisasterOS")
st.markdown('<div class="product-description">A local resilience console that monitors the host, explains risk, '
            'recovers only managed workloads, verifies the outcome, and records an audit trail.</div>',
            unsafe_allow_html=True)


@st.fragment(run_every="2s")
def dashboard():
    snap, history, error = system.snapshot()
    if error:
        st.error(f"Monitoring paused: {error}. Check the event history, then restart the app if the error persists.")
    if not snap:
        st.info("Collecting the first system sample. This normally takes up to two seconds.")
        return

    current = snap["observed"]
    scenario = current.get("scenario")
    status = snap["risk"]["category"]
    fields = current.get("simulated_fields", [])
    context = f"Scenario active: {scenario}" if scenario else "Live host monitoring"
    detail = (f"Simulated fields: {', '.join(fields)}" if fields else "All resource measurements are real")
    source = "Mixed data" if fields else "Measured data"
    st.markdown(
        f'<div class="status-surface"><div><div class="status-label">Current risk</div>'
        f'<div class="status-value {status.lower()}">{html.escape(status)}</div></div>'
        f'<div><div class="status-context">{html.escape(context)}</div>'
        f'<div class="status-detail">{html.escape(detail)} · {html.escape(system.backend.name.title())} backend</div></div>'
        f'<div class="source-tag">{html.escape(source)}</div></div>', unsafe_allow_html=True)

    overview, intelligence, simulator_tab, recovery_tab, platform_tab, benchmark_tab, events_tab = st.tabs(
        ["Overview", "Intelligence", "Simulator", "Recovery", "Platform", "Benchmarks", "Activity"])

    with overview:
        st.subheader("Host resources")
        st.caption("Live measurements refresh every two seconds. Scenario-modified fields are identified above.")
        a, b, c, d = st.columns(4)
        a.metric("CPU utilization", f'{current["cpu"]:.1f}%')
        b.metric("Memory utilization", f'{current["ram"]:.1f}%')
        c.metric("Disk utilization", f'{current["disk"]:.1f}%')
        d.metric("Memory available", f'{current["available_mb"] / 1024:.1f} GB')
        e, f, g = st.columns(3)
        e.metric("Swap utilization", f'{current["swap"]:.1f}%')
        f.metric("Network upload", f'{current["network_up"]:.1f} KB/s')
        g.metric("Network download", f'{current["network_down"]:.1f} KB/s')
        if history:
            frame = pd.DataFrame(history)
            fig = go.Figure()
            for key, label, color in [("cpu", "CPU", "#45d5c3"), ("ram", "Memory", "#79aaff"), ("disk", "Disk", "#f3bd67")]:
                fig.add_trace(go.Scatter(x=frame["ts"], y=frame[key], mode="lines", name=label,
                                         line={"color": color, "width": 2}))
            fig.update_layout(template="plotly_dark", paper_bgcolor="#101c2e", plot_bgcolor="#101c2e",
                              yaxis={"range": [0, 100], "title": "Utilization (%)", "gridcolor": "#2a3c57"},
                              xaxis={"gridcolor": "#1a2b42"}, height=330,
                              margin={"l": 20, "r": 20, "t": 30, "b": 20}, legend={"orientation": "h"})
            st.plotly_chart(fig, width="stretch")
        st.caption("Network values are rates calculated from counter differences, not cumulative totals.")

    with intelligence:
        st.subheader("Detection and risk")
        anomaly = snap["anomaly"]
        a, b, c = st.columns(3)
        a.metric("Anomaly detector", anomaly["status"])
        b.metric("Isolation Forest score", "Learning" if anomaly["score"] is None else f'{anomaly["score"]:.3f}')
        c.metric("Overall risk", status)
        samples = anomaly["samples"]
        st.progress(min(samples / 20, 1.0), text=f"Real baseline samples: {samples}/20")
        if samples < 20:
            st.info(f"The detector needs {20 - samples} more real sample(s) before anomaly scoring begins. Simulations do not train it.")
        else:
            st.caption("A negative score is unusual relative to this session's baseline. It is not a failure probability.")
        st.subheader("Why the risk has this level")
        cols = st.columns(3)
        for col, (name, level) in zip(cols, snap["risk"]["resources"].items()):
            col.metric(f"{name} risk", level)
        for reason in snap["risk"]["reasons"]:
            st.markdown(f"- {reason}")
        st.caption(snap["risk"]["method"])

    with simulator_tab:
        st.subheader("Run a controlled failure")
        st.caption("Every scenario is bounded to application-owned processes. The host network and user files are never changed.")
        busy = system.benchmarker.running
        if busy:
            st.info("A benchmark is running. Scenario controls will unlock when both trials finish.")
        for row in (SCENARIOS[:3], SCENARIOS[3:]):
            cols = st.columns(3)
            for col, name in zip(cols, row):
                with col:
                    st.markdown(f"**{name}**")
                    st.caption(SCENARIO_HELP[name])
                    if st.button(f"Start {name}", key=f"scenario_{name}", width="stretch", disabled=busy):
                        try:
                            system.start(name)
                            st.toast(f"{name} started")
                            st.rerun(scope="fragment")
                        except Exception as exc:
                            st.error(f"Could not start {name}: {exc}")
        st.divider()
        a, b, c = st.columns([1, 1, 1])
        a.metric("Active scenario", scenario or "None")
        b.metric("Demo service", "Running" if snap["service_alive"] else "Stopped")
        c.metric("Test dependency", "Available" if current["dependency_online"] else "Unavailable")
        if st.button("Stop active simulation", type="primary", disabled=busy or not scenario):
            system.stop()
            st.rerun(scope="fragment")
        st.caption("CPU and memory workers run for at most 25 seconds and allocate at most 64 MB.")

    with recovery_tab:
        st.subheader("Recovery flow")
        st.caption("Read left to right: the policy chooses an allowlisted response, executes it, then checks the result.")
        decision = snap["decision"]
        action = snap["action"]
        episode_action = action["action"] if action else decision["action"]
        episode_priority = action["priority"] if action else decision["priority"]
        episode_reason = action.get("details", {}).get("reason", decision["reason"]) if action else decision["reason"]
        decision_value = action_label(episode_action)
        if action:
            execution_value = action_label(action["action"])
            started = format_local_time(action.get("started_at"))
            execution_detail = (f'Started {started} · {format_duration_ms(action.get("duration_ms"))}'
                                if started != "—" else f'Completed in {format_duration_ms(action.get("duration_ms"))}')
            verification_value = action["result"].title()
            verification_detail = ("Post-action check passed" if action["result"] == "SUCCESSFUL" else
                                   "Decision logged without a verified change" if action["result"] == "UNVERIFIED" else
                                   action.get("details", {}).get("error", "Post-action check failed"))
            verification_tone = {"SUCCESSFUL": "success", "UNVERIFIED": "warning", "FAILED": "danger"}.get(action["result"], "neutral")
        else:
            execution_value, execution_detail = "Waiting", "No recovery action has run in this session"
            verification_value, verification_detail, verification_tone = "Not required", "The managed workloads are currently healthy", "neutral"
        st.markdown('<div class="recovery-flow">' +
                    stage_panel("Step 1", "Policy decision", decision_value,
                                f'{episode_priority} · {episode_reason}') +
                    stage_panel("Step 2", "Execution", execution_value, execution_detail,
                                "success" if action and action["result"] == "SUCCESSFUL" else "neutral") +
                    stage_panel("Step 3", "Verification", verification_value, verification_detail, verification_tone) +
                    '</div>', unsafe_allow_html=True)
        dry_run = st.toggle("Dry run — record decisions without changing managed workloads", value=system.recovery.dry_run)
        if dry_run != system.recovery.dry_run:
            system.set_dry_run(dry_run)
        if dry_run:
            st.warning("Dry run is active. New decisions will be recorded as unverified and no recovery command will run.")
        left, right = st.columns(2)
        left.metric("Offline fallback", "Enabled" if snap["offline_mode"] else "Disabled")
        left.caption("Used only for the simulated dependency failure.")
        attempts = system.recovery.attempts.get((scenario, episode_action), 0)
        right.metric("Attempts for current decision", f"{attempts} / {system.recovery.MAX_RETRIES}")
        right.caption(f"Retries wait at least {system.recovery.COOLDOWN_SECONDS} seconds.")
        with st.expander("Safety boundaries and result meanings"):
            st.markdown("""
            - **Successful:** the requested action ran and its post-action health check passed.
            - **Failed:** execution or verification failed. Retries are bounded and failures are audited.
            - **Unverified:** the system logged a dry-run or recommendation-only action without claiming recovery.
            - Actions target only registered demo workloads. Disk pressure never deletes files.
            """)

    with platform_tab:
        st.subheader(f"{system.backend.name.title()} capabilities")
        st.caption("Statuses describe what this machine and the current permissions can actually do.")
        try:
            capabilities = system.backend.get_platform_capabilities()
            capability_rows = [{"Capability": feature.replace("_", " ").title(),
                                "Status": info["status"].replace("_", " ").title(),
                                "Explanation": info["reason"]} for feature, info in capabilities.items()]
            st.dataframe(pd.DataFrame(capability_rows), hide_index=True, width="stretch")
            with st.expander("What the capability statuses mean"):
                st.markdown("**Supported** is ready now. **Not configured** needs optional setup. **Unsupported** is unavailable in this environment. **Permission denied** needs delegated access. **Not applicable** belongs to another platform.")
            st.subheader("Managed workloads")
            workloads = system.backend.list_managed_workloads()
            if workloads:
                workload_rows = []
                for item in workloads:
                    workload_rows.append({"Workload": item.get("id", "—").replace("_", " ").title(),
                                          "Priority": item.get("priority", "—"),
                                          "Health": "Running" if item.get("alive") is True else "Stopped" if item.get("alive") is False else item.get("status", "Unknown"),
                                          "CPU": "—" if item.get("cpu_percent") is None else f'{item["cpu_percent"]:.1f}%',
                                          "Memory": "—" if item.get("memory_mb") is None else f'{item["memory_mb"]:.1f} MB'})
                st.dataframe(pd.DataFrame(workload_rows), hide_index=True, width="stretch")
            else:
                st.info("No managed workloads are registered.")
            if system.backend.name == "linux" and capabilities["systemd"]["status"] != "SUPPORTED":
                st.info("User-systemd recovery is unavailable: " + capabilities["systemd"]["reason"])
            if system.backend.name == "linux":
                worker = system.backend.inspect_workload("stress_worker")
                if worker and worker.get("alive"):
                    st.subheader("Managed worker limits")
                    cpu_limit = st.slider("CPU quota (% of one core)", 10, 100, 50, 5)
                    memory_limit = st.slider("Memory limit (MB)", 128, 1024, 256, 64)
                    left, right = st.columns(2)
                    if left.button("Apply CPU quota", disabled=capabilities["cpu_quota"]["status"] != "SUPPORTED" or system.recovery.dry_run):
                        outcome = system.apply_policy("cpu_quota", cpu_limit)
                        st.success("CPU quota applied and verified.") if outcome.succeeded else st.error(outcome.error)
                    if right.button("Apply memory limit", disabled=capabilities["memory_limit"]["status"] != "SUPPORTED" or system.recovery.dry_run):
                        outcome = system.apply_policy("memory_limit", memory_limit)
                        st.success("Memory limit applied and verified.") if outcome.succeeded else st.error(outcome.error)
        except Exception as exc:
            st.error(f"Capability inspection failed: {exc}")

    with benchmark_tab:
        st.subheader("Recovery comparison")
        st.caption("Runs the same managed service crash twice: first without automated recovery, then with it enabled.")
        if st.button("Run service-crash comparison", disabled=system.benchmarker.running):
            system.benchmarker.start_pair()
            st.rerun(scope="fragment")
        if system.benchmarker.running:
            st.info("Benchmark running in the background. This usually takes about 10 seconds.")
        if system.benchmarker.error:
            st.error(f"Benchmark failed: {system.benchmarker.error}")
        try:
            runs = system.db.benchmarks(50)
            if runs:
                table = pd.DataFrame(runs)
                latest_v2 = table[table["method_version"] >= 2]
                enabled_rows = latest_v2[latest_v2["recovery_enabled"] == 1]
                disabled_rows = latest_v2[latest_v2["recovery_enabled"] == 0]
                if not enabled_rows.empty and not disabled_rows.empty:
                    enabled = enabled_rows.iloc[0]
                    disabled = disabled_rows.iloc[0]
                    a, b, c = st.columns(3)
                    a.metric("Detection latency", format_duration_ms(enabled["detection_latency_ms"]))
                    b.metric("Verified recovery latency", format_duration_ms(enabled["recovery_latency_ms"]))
                    c.metric("Availability with recovery", f'{enabled["availability_percent"]:.1f}%')
                    st.caption(f'Latest disabled trial availability: {disabled["availability_percent"]:.1f}%. Latencies are measured with a monotonic clock; displayed timestamps are local time.')
                display = table[["ts", "recovery_enabled", "detection_latency_ms", "recovery_latency_ms",
                                 "availability_percent", "success", "false_positives", "missed_detections", "method_version"]].copy()
                display["ts"] = display["ts"].map(format_local_time)
                display["recovery_enabled"] = display["recovery_enabled"].map({1: "Enabled", 0: "Disabled"})
                display["detection_latency_ms"] = display["detection_latency_ms"].map(format_duration_ms)
                display["recovery_latency_ms"] = display["recovery_latency_ms"].map(format_duration_ms)
                display["success"] = display["success"].map({1: "Verified", 0: "Not recovered"})
                display.columns = ["Run time", "Recovery", "Detection latency", "Fault → verified", "Availability %",
                                   "Outcome", "False positives", "Missed detections", "Method"]
                st.dataframe(display, hide_index=True, width="stretch")
                chart = latest_v2.dropna(subset=["availability_percent"]).copy()
                if not chart.empty:
                    fig = go.Figure(go.Bar(x=["Recovery enabled" if value else "Recovery disabled" for value in chart["recovery_enabled"]],
                                           y=chart["availability_percent"], marker_color="#45d5c3"))
                    fig.update_layout(template="plotly_dark", paper_bgcolor="#101c2e", plot_bgcolor="#101c2e",
                                      yaxis={"range": [0, 100], "title": "Observed availability (%)", "gridcolor": "#2a3c57"},
                                      height=290, margin={"l": 20, "r": 20, "t": 20, "b": 20})
                    st.plotly_chart(fig, width="stretch")
                if (table["method_version"] < 2).any():
                    st.caption("Older method 1 rows are retained for audit history but excluded from the chart.")
                with st.expander("How timing and availability are calculated"):
                    st.markdown("**Detection latency** runs from fault injection to the policy decision. **Fault → verified** runs from injection to the completed health check. Both use `time.monotonic()` so clock changes cannot distort elapsed time. Availability is the share of unique monitor observations where the managed service is running, including the injected fault state.")
            else:
                st.info("No benchmark results yet. Run the comparison to create the first measured pair.")
        except Exception as exc:
            st.error(f"Could not read benchmark results: {exc}")

    with events_tab:
        st.subheader("Audit activity")
        st.caption("Newest events appear first. Details remain in SQLite even when the concise table hides them.")
        try:
            events = system.db.events(100)
            if events:
                event_table = pd.DataFrame(events)
                event_table["ts"] = event_table["ts"].map(format_local_time)
                event_table["duration_ms"] = event_table["duration_ms"].map(format_duration_ms)
                display = event_table[["ts", "severity", "scenario", "action", "result", "duration_ms"]].copy()
                display.columns = ["Local time", "Severity", "Scenario", "Action", "Result", "Duration"]
                st.dataframe(display, width="stretch", hide_index=True)
                latest = events[0]
                with st.expander("Latest event details"):
                    st.write(latest.get("details") or "No additional details were recorded.")
            else:
                st.info("No events have been recorded yet.")
        except Exception as exc:
            st.error(f"Could not read activity: {exc}")


dashboard()
