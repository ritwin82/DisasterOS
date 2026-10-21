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
from benchmarking import BenchmarkRunner, summarize_benchmark_rows
from config import (BASELINE_SAMPLES, BENCHMARK_TRIALS, HISTORY_LIMIT,
                    MODEL_PATH, SAMPLE_SECONDS, SCENARIOS)
from database import Database
from disaster_simulator import DisasterSimulator
from platform_backends.factory import create_backend
from recovery_engine import RecoveryEngine
from risk_engine import RiskEngine


class DisasterOS:
    def __init__(self):
        self.db = Database()
        self.detector = AnomalyDetector(model_path=MODEL_PATH)
        self.detector.bootstrap(self.db.clean_metrics(BASELINE_SAMPLES))
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
                anomaly = self.detector.assess(real, simulated=observed["simulated"], score_sample=observed)
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


def telemetry_panel(label, value, percent, tone="normal"):
    safe_label = html.escape(str(label))
    safe_value = html.escape(str(value))
    width = min(max(float(percent), 0.0), 100.0)
    return (f'<div class="telemetry-item {tone}"><div class="telemetry-label">{safe_label}</div>'
            f'<div class="telemetry-value">{safe_value}</div><div class="telemetry-track">'
            f'<span style="width:{width:.1f}%"></span></div></div>')


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
    --canvas: #0b1120; --surface: #091315; --surface-raised: #0d1a1d;
    --surface-hover: #112226; --line: #1b3437; --line-strong: #285055;
    --text: #e6efed; --muted: #8da3a0; --quiet: #78908c;
    --green: #58d39b; --blue: #69aeda; --amber: #e6ad4b; --red: #e56d72;
  }
  * { box-sizing: border-box; }
  html, body, [class*="css"] { font-family: "Segoe UI Variable", "Segoe UI", system-ui, sans-serif; }
  .stApp { background: var(--canvas); color: var(--text); }
  [data-testid="stHeader"] { background: var(--canvas); height: 2rem; }
  [data-testid="stDecoration"] { display: none; }
  .block-container { max-width: 1500px; padding: 3.25rem 1.6rem 4rem; }
  h1, h2, h3 { color: var(--text) !important; letter-spacing: -.015em; }
  h2 { font-size: 1.15rem !important; margin: 1.35rem 0 .15rem !important; font-weight: 650 !important; }
  h3 { font-size: .98rem !important; margin-top: 1.2rem !important; }
  p, li { line-height: 1.48; }
  ::selection { background: #58d39b35; color: var(--text); }
  input, textarea, [contenteditable="true"] { caret-color: var(--green); }
  a { color: var(--green); text-decoration-thickness: 1px; text-underline-offset: .18em; }
  a:hover { text-decoration-thickness: 2px; }
  :focus-visible { outline: 2px solid var(--green) !important; outline-offset: 2px !important; }
  .console-header {
    display: flex; align-items: flex-end; justify-content: space-between; gap: 2rem;
    border-bottom: 1px solid var(--line-strong); padding: .2rem 0 1rem; margin-bottom: .9rem;
  }
  .console-brand { color: var(--text); font-size: 1.65rem; line-height: 1; font-weight: 720; letter-spacing: -.025em; }
  .console-brand span { color: var(--green); }
  .console-subtitle { color: var(--muted); font-size: .83rem; margin-top: .45rem; }
  .console-meta { display: flex; gap: .45rem; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
  .console-meta span, .source-tag {
    border: 1px solid var(--line); border-radius: 999px; padding: .32rem .58rem;
    color: var(--muted); font-size: .68rem; font-weight: 650; letter-spacing: .065em; text-transform: uppercase;
  }
  .status-surface {
    display: grid; grid-template-columns: 150px 1fr auto; gap: 1rem; align-items: center;
    background: var(--surface); border: 1px solid var(--line); border-radius: 7px;
    padding: .72rem .85rem; margin: .6rem 0 .8rem;
  }
  .status-label, .stage-step, .telemetry-label { color: var(--quiet); font-size: .66rem; font-weight: 700; letter-spacing: .085em; text-transform: uppercase; }
  .status-value { font-size: 1rem; font-weight: 720; margin-top: .14rem; letter-spacing: .02em; }
  .status-value.low { color: var(--green); } .status-value.medium { color: var(--amber); }
  .status-value.high, .status-value.critical { color: var(--red); }
  .status-context { color: var(--text); font-size: .86rem; font-weight: 630; }
  .status-detail { color: var(--muted); margin-top: .12rem; font-size: .76rem; }
  .source-tag { white-space: nowrap; color: var(--green); border-color: #285a47; }
  .telemetry-grid { display: grid; grid-template-columns: repeat(4, 1fr); border: 1px solid var(--line); border-radius: 7px; overflow: hidden; margin: .75rem 0; }
  .telemetry-item { min-width: 0; background: var(--surface); padding: .85rem .9rem; border-right: 1px solid var(--line); }
  .telemetry-item:last-child { border-right: 0; }
  .telemetry-value { color: var(--text); font-size: 1.45rem; line-height: 1.1; font-weight: 680; margin: .42rem 0 .6rem; font-variant-numeric: tabular-nums; }
  .telemetry-track { height: 3px; background: #16282b; overflow: hidden; }
  .telemetry-track span { display: block; height: 100%; background: var(--green); transition: width 240ms ease-out, background-color 160ms ease-out; }
  .telemetry-item.warn .telemetry-track span { background: var(--amber); }
  .telemetry-item.danger .telemetry-track span { background: var(--red); }
  .model-readiness {
    display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: .8rem 1.5rem; align-items: center;
    background: var(--surface); border: 1px solid var(--line); border-radius: 7px; padding: .8rem .9rem; margin: .75rem 0;
  }
  .readiness-title { color: var(--text); font-size: .9rem; font-weight: 680; margin-top: .16rem; }
  .readiness-detail { color: var(--muted); font-size: .75rem; margin-top: .2rem; line-height: 1.4; }
  .readiness-count { color: var(--text); font-size: 1.15rem; font-weight: 700; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .readiness-count span { color: var(--quiet); font-size: .72rem; font-weight: 620; }
  .readiness-track { grid-column: 1 / -1; height: 3px; background: var(--telemetry-track); overflow: hidden; }
  .readiness-track span { display: block; height: 100%; background: var(--green); transition: width 240ms ease-out; }
  div[data-testid="stMetric"] { background: var(--surface); border: 1px solid var(--line); border-radius: 7px; padding: .72rem .8rem; box-shadow: none; }
  div[data-testid="stMetricLabel"] { color: var(--muted); font-size: .72rem; }
  div[data-testid="stMetricValue"] { color: var(--text); font-size: 1.35rem; font-variant-numeric: tabular-nums; }
  .recovery-flow { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0; margin: .75rem 0 1rem; border: 1px solid var(--line); border-radius: 7px; overflow: hidden; }
  .recovery-stage { background: var(--surface); border-right: 1px solid var(--line); padding: .85rem; min-height: 132px; }
  .recovery-stage:last-child { border-right: 0; }
  .recovery-stage.success { background: #0b1b17; } .recovery-stage.warning { background: #1c180e; }
  .recovery-stage.danger { background: #1c1012; }
  .stage-title { color: var(--muted); margin-top: .62rem; font-size: .76rem; }
  .stage-value { color: var(--text); font-size: 1rem; font-weight: 680; margin-top: .18rem; }
  .stage-detail { color: var(--muted); margin-top: .45rem; font-size: .76rem; line-height: 1.4; }
  [data-baseweb="tab-list"] { gap: 0; overflow-x: auto; scrollbar-width: thin; border-bottom: 1px solid var(--line); }
  [data-baseweb="tab"] { min-height: 42px; white-space: nowrap; padding: 0 .9rem; color: var(--muted); font-size: .75rem; }
  button[role="tab"][aria-selected="true"], button[role="tab"][aria-selected="true"] p { color: var(--green) !important; background: #0a1516; }
  [data-baseweb="tab-highlight"] { background-color: var(--green) !important; height: 2px; }
  [data-testid="stVerticalBlockBorderWrapper"] { border-color: var(--line) !important; border-radius: 7px !important; background: var(--surface); }
  .stButton button { border-radius: 5px; min-height: 38px; border: 1px solid var(--line-strong); background: #0e2022; color: var(--text); font-weight: 620; transition: border-color 140ms ease, background-color 140ms ease, color 140ms ease; }
  .stButton button:hover { border-color: var(--green); color: var(--green); background: var(--surface-hover); }
  .stButton button[kind="primary"] { background: var(--green); border-color: var(--green); color: #04100c; font-weight: 720; }
  .stButton button[kind="primary"]:hover { background: #70dfaa; border-color: #70dfaa; color: #04100c; }
  .stButton button:disabled { opacity: .45; }
  [data-testid="stDataFrame"] { border: 1px solid var(--line); border-radius: 7px; overflow: hidden; }
  [data-testid="stExpander"] { border: 1px solid var(--line) !important; border-radius: 7px !important; background: var(--surface); }
  [data-testid="stAlert"] { border-radius: 6px; border-width: 1px; }
  [data-testid="stProgress"] > div > div { background: var(--green); }
  hr { border-color: var(--line); margin: 1rem 0; }
  small, .stCaption { color: var(--muted) !important; }
  * { scrollbar-color: var(--line-strong) var(--canvas); scrollbar-width: thin; }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; animation-iteration-count: 1 !important; }
  }
  @media (max-width: 800px) {
    .block-container { padding: 3rem .75rem 3rem; }
    .console-header { align-items: flex-start; flex-direction: column; gap: .8rem; }
    .console-meta { justify-content: flex-start; width: 100%; }
    .status-surface { grid-template-columns: 1fr; gap: .8rem; }
    .source-tag { width: fit-content; }
    .telemetry-grid { grid-template-columns: repeat(2, 1fr); }
    .model-readiness { grid-template-columns: 1fr; gap: .6rem; }
    .readiness-track { grid-column: 1; }
    .telemetry-item:nth-child(2) { border-right: 0; }
    .telemetry-item:nth-child(-n+2) { border-bottom: 1px solid var(--line); }
    .recovery-flow { grid-template-columns: 1fr; }
    .recovery-stage { border-right: 0; border-bottom: 1px solid var(--line); }
    .recovery-stage:last-child { border-bottom: 0; }
  }
</style>
""", unsafe_allow_html=True)

system = engine()
st.markdown('<header class="console-header"><div><div class="console-brand">Disaster<span>OS</span></div>'
            '<div class="console-subtitle">Local resilience control plane for monitored and managed workloads</div></div>'
            '<div class="console-meta"><span>Local environment</span><span>2 second telemetry</span>'
            '<span>Bounded recovery</span></div></header>', unsafe_allow_html=True)


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
        f'<div class="status-detail">{html.escape(detail)} · {html.escape(system.backend.name.title())} backend · last sample {html.escape(format_local_time(current.get("ts")))}</div></div>'
        f'<div class="source-tag">{html.escape(source)}</div></div>', unsafe_allow_html=True)

    overview, intelligence, simulator_tab, recovery_tab, platform_tab, benchmark_tab, events_tab = st.tabs(
        ["Fleet overview", "Detection", "Failure lab", "Recovery", "Infrastructure", "Benchmarks", "Audit log"])

    with overview:
        st.subheader("Host telemetry")
        st.caption("Measured utilization, refreshed every two seconds. Scenario-modified fields remain identified in the status bar.")
        def resource_tone(value):
            return "danger" if value >= 90 else "warn" if value >= 75 else "normal"
        telemetry = "".join([
            telemetry_panel("CPU utilization", f'{current["cpu"]:.1f}%', current["cpu"], resource_tone(current["cpu"])),
            telemetry_panel("Memory utilization", f'{current["ram"]:.1f}%', current["ram"], resource_tone(current["ram"])),
            telemetry_panel("Disk utilization", f'{current["disk"]:.1f}%', current["disk"], resource_tone(current["disk"])),
            telemetry_panel("Swap utilization", f'{current["swap"]:.1f}%', current["swap"], resource_tone(current["swap"])),
        ])
        st.markdown(f'<div class="telemetry-grid">{telemetry}</div>', unsafe_allow_html=True)
        e, f, g = st.columns(3)
        e.metric("Available memory", f'{current["available_mb"] / 1024:.1f} GB')
        f.metric("Outbound rate", f'{current["network_up"]:.1f} KB/s')
        g.metric("Inbound rate", f'{current["network_down"]:.1f} KB/s')
        if history:
            frame = pd.DataFrame(history)
            fig = go.Figure()
            for key, label, color in [("cpu", "CPU", "#58d39b"), ("ram", "Memory", "#69aeda"), ("disk", "Disk", "#e6ad4b")]:
                fig.add_trace(go.Scatter(x=frame["ts"], y=frame[key], mode="lines", name=label,
                                         line={"color": color, "width": 1.6}))
            fig.update_layout(template="plotly_dark", paper_bgcolor="#091315", plot_bgcolor="#091315",
                              yaxis={"range": [0, 100], "title": "Utilization (%)", "gridcolor": "#173033", "zeroline": False},
                              xaxis={"gridcolor": "#102326", "zeroline": False}, height=310,
                              font={"family": "IBM Plex Sans, Segoe UI, sans-serif", "color": "#8da3a0", "size": 11},
                              margin={"l": 20, "r": 20, "t": 28, "b": 20}, legend={"orientation": "h", "y": 1.12})
            st.plotly_chart(fig, width="stretch")
        st.caption("Network values are rates calculated from counter differences, not cumulative totals.")

    with intelligence:
        st.subheader("Detection and risk")
        anomaly = snap["anomaly"]
        a, b, c = st.columns(3)
        a.metric("Anomaly detector", anomaly["status"])
        b.metric("Anomaly score", "Learning" if anomaly["score"] is None else f'{anomaly["score"]:.3f}')
        c.metric("Overall risk", status)
        samples = anomaly["samples"]
        required = anomaly["required_samples"]
        readiness_pct = min((samples / required) * 100, 100) if required else 100
        remaining = max(required - samples, 0)
        readiness_title = "Ready for anomaly scoring" if not remaining else "Learning normal system behavior"
        readiness_detail = (
            "The model has enough live observations to identify unusual behavior. Simulated scenarios remain excluded from training."
            if not remaining else
            f"{remaining} more live observation{'s' if remaining != 1 else ''} needed. Simulated scenarios are evaluated but never included in training."
        )
        st.markdown(
            '<section class="model-readiness" aria-label="Detector readiness">'
            '<div><div class="telemetry-label">Detector readiness</div>'
            f'<div class="readiness-title">{readiness_title}</div>'
            f'<div class="readiness-detail">{readiness_detail}</div></div>'
            f'<div class="readiness-count">{samples}<span> / {required} live observations</span></div>'
            f'<div class="readiness-track" aria-hidden="true"><span style="width:{readiness_pct:.1f}%"></span></div>'
            '</section>',
            unsafe_allow_html=True,
        )
        if samples < required:
            st.caption("Anomaly scoring starts automatically when the operating baseline is ready.")
        else:
            threshold = anomaly.get("threshold")
            threshold_text = "—" if threshold is None else f"{threshold:.3f}"
            st.caption(f"Model v{anomaly['model_version']} · {anomaly.get('algorithm', 'Anomaly model')} · {anomaly['feature_count']} features · calibrated threshold {threshold_text} · source: {anomaly['model_source']}. A lower-than-threshold score is unusual; it is not a failure probability.")
            with st.expander("Model training details"):
                calibrated_rate = anomaly.get("calibration_false_positive_rate")
                st.markdown(
                    f"- Trained: {format_local_time(anomaly.get('trained_at'))}\n"
                    f"- Time-ordered training samples: {anomaly.get('train_count', '—')}\n"
                    f"- Later calibration samples: {anomaly.get('calibration_count', '—')}\n"
                    f"- Calibration false-positive rate: {'—' if calibrated_rate is None else f'{100 * calibrated_rate:.1f}%'}\n"
                    "- Simulated observations can be scored, but cannot change the saved model."
                )
                if anomaly.get("backend_note"):
                    st.info("Portable ML fallback is active because the scikit-learn native backend is unavailable on this machine.")
        st.subheader("Why the risk has this level")
        cols = st.columns(3)
        for col, (name, level) in zip(cols, snap["risk"]["resources"].items()):
            col.metric(f"{name} risk", level)
        for reason in snap["risk"]["reasons"]:
            st.markdown(f"- {reason}")
        st.caption(snap["risk"]["method"])

    with simulator_tab:
        st.subheader("Controlled failure lab")
        st.caption("Inject a bounded condition, observe the policy decision, and verify recovery. Host networking and user files are never modified.")
        busy = system.benchmarker.running
        if busy:
            st.info("A benchmark suite is running. Scenario controls will unlock when all trials finish.")
        for row in (SCENARIOS[:3], SCENARIOS[3:]):
            cols = st.columns(3)
            for col, name in zip(cols, row):
                with col:
                    with st.container(border=True):
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
        st.caption("Safety envelope: CPU and memory workers stop after 25 seconds; memory allocation is capped at 64 MB.")

    with recovery_tab:
        st.subheader("Recovery control loop")
        st.caption("Policy selection, bounded execution, and post-action verification are shown as one auditable sequence.")
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
        st.subheader("Recovery benchmark")
        st.caption("Runs matched service-crash trials with recovery disabled and enabled. Trial order alternates to reduce warm-up bias.")
        trial_count = st.slider("Comparison repetitions", 3, 10, BENCHMARK_TRIALS,
                                help="Each repetition contains one recovery-disabled run and one recovery-enabled run.",
                                disabled=system.benchmarker.running)
        if st.button("Run benchmark suite", disabled=system.benchmarker.running):
            system.benchmarker.start_suite(trial_count)
            st.rerun(scope="fragment")
        if system.benchmarker.running:
            st.progress(system.benchmarker.progress,
                        text=f"Completed {system.benchmarker.completed_runs} of {system.benchmarker.total_runs} runs")
            st.info("Benchmark running in the background. Five repetitions usually take about 45–60 seconds.")
        if system.benchmarker.error:
            st.error(f"Benchmark failed: {system.benchmarker.error}")
        try:
            runs = system.db.benchmarks(200)
            if runs:
                table = pd.DataFrame(runs)
                current = table[(table["method_version"] >= 3) & table["batch_id"].notna()]
                latest_batch = None if current.empty else current.iloc[0]["batch_id"]
                latest = current[current["batch_id"] == latest_batch] if latest_batch else pd.DataFrame()
                summary = summarize_benchmark_rows(latest.to_dict("records")) if not latest.empty else {}
                if True in summary and False in summary:
                    enabled, disabled = summary[True], summary[False]
                    a, b, c, d = st.columns(4)
                    a.metric("Median detection", format_duration_ms(enabled["detection_median_ms"]),
                             help="Middle enabled-run detection latency; less sensitive to one slow run.")
                    b.metric("P95 recovery", format_duration_ms(enabled["recovery_p95_ms"]),
                             help="95th-percentile fault-to-verified latency; highlights tail performance.")
                    c.metric("Recovery success", f'{enabled["success_rate"]:.0f}%')
                    d.metric("Mean availability", f'{enabled["availability_mean"]:.1f}%')
                    delta = enabled["availability_mean"] - disabled["availability_mean"]
                    st.caption(f"Latest batch: {enabled['runs']} enabled + {disabled['runs']} disabled runs. Recovery changed mean observed availability by {delta:+.1f} percentage points. Timing uses a monotonic clock.")
                    aggregate = pd.DataFrame([
                        {"Mode": "Recovery disabled", **disabled},
                        {"Mode": "Recovery enabled", **enabled},
                    ])
                    aggregate = aggregate[["Mode", "runs", "detection_median_ms", "detection_p95_ms",
                                           "recovery_median_ms", "recovery_p95_ms", "availability_mean",
                                           "success_rate", "false_positives", "missed_detections"]]
                    aggregate.columns = ["Mode", "Runs", "Detection median (ms)", "Detection p95 (ms)",
                                         "Recovery median (ms)", "Recovery p95 (ms)", "Mean availability (%)",
                                         "Success rate (%)", "False positives", "Missed detections"]
                    st.dataframe(aggregate, hide_index=True, width="stretch")
                    fig = go.Figure(go.Bar(x=aggregate["Mode"], y=aggregate["Mean availability (%)"],
                                           marker_color=["#e6ad4b", "#58d39b"]))
                    fig.update_layout(template="plotly_dark", paper_bgcolor="#091315", plot_bgcolor="#091315",
                                      font={"family": "IBM Plex Sans, Segoe UI, sans-serif", "color": "#8da3a0", "size": 11},
                                      yaxis={"range": [0, 100], "title": "Mean observed availability (%)", "gridcolor": "#173033", "zeroline": False},
                                      height=290, margin={"l": 20, "r": 20, "t": 20, "b": 20}, showlegend=False)
                    st.plotly_chart(fig, width="stretch")
                display = table[["ts", "batch_id", "trial_number", "recovery_enabled", "detection_latency_ms", "recovery_latency_ms",
                                 "availability_percent", "success", "false_positives", "missed_detections", "method_version"]].copy()
                display["ts"] = display["ts"].map(format_local_time)
                display["recovery_enabled"] = display["recovery_enabled"].map({1: "Enabled", 0: "Disabled"})
                display["detection_latency_ms"] = display["detection_latency_ms"].map(format_duration_ms)
                display["recovery_latency_ms"] = display["recovery_latency_ms"].map(format_duration_ms)
                display["success"] = display["success"].map({1: "Verified", 0: "Not recovered"})
                display["batch_id"] = display["batch_id"].map(lambda value: "Legacy" if pd.isna(value) else str(value)[:8])
                display.columns = ["Run time", "Batch", "Trial", "Recovery", "Detection latency", "Fault → verified", "Availability %",
                                   "Outcome", "False positives", "Missed detections", "Method"]
                with st.expander("Raw trial measurements"):
                    st.dataframe(display, hide_index=True, width="stretch")
                if (table["method_version"] < 3).any():
                    st.caption("Older method 1–2 rows are retained for audit history but excluded from repeated-trial aggregates.")
                with st.expander("How timing and availability are calculated"):
                    st.markdown("**Detection latency** runs from fault injection to the policy decision. **Fault → verified** runs from injection to the completed health check. Both use `time.monotonic()` so clock changes cannot distort elapsed time. Availability is the share of unique monitor observations where the managed service is running, including the injected fault state. The median represents typical behavior; p95 exposes slow-tail behavior; success rate includes failed recoveries instead of silently dropping them.")
            else:
                st.info("No benchmark results yet. Run a suite to create measured repeated trials.")
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
