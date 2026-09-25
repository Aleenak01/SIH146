"""
End-to-end check and demo of the whole platform, against a real server process and a throw-away database.

    .\\.venv\\Scripts\\python.exe scripts\\e2e_check.py

It never touches data/sih146.db, never contacts the internet (the optional real Bitcoin source stays off), and takes
about two minutes. Each line it prints is a check that passed; the first failure stops it with a non-zero exit code.
It follows the investigator's path: import -> monitoring/analysis -> leads -> clusters/graph -> search -> case ->
new data arriving -> restart -> the real-data source staying off.
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8017
BASE = f"http://127.0.0.1:{PORT}"
T0 = time.time()


def say(msg: str) -> None:
    print(f"[{time.time() - T0:6.1f}s] {msg}", flush=True)


def check(cond: bool, msg: str) -> None:
    if not cond:
        print(f"FAILED: {msg}", flush=True)
        raise SystemExit(1)
    say(f"ok  {msg}")


def call(method: str, path: str, body=None, timeout: float = 300):
    req = urllib.request.Request(BASE + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def get(path: str, **params):
    q = "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}) if params else ""
    status, body = call("GET", path + q)
    assert status == 200, (path, status, body)
    return body


def wait(cond, what: str, timeout: float = 300):
    end = time.time() + timeout
    while time.time() < end:
        st = get("/api/monitor/status")
        if cond(st):
            return st
        time.sleep(1)
    raise SystemExit(f"FAILED: timed out waiting for {what}")


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="sih146-e2e-"))
    env = {**os.environ, "SIH146_DB_PATH": str(work / "e2e.db"), "SIH146_INBOX_DIR": str(work / "inbox"), "SIH146_NETWORK_CSV": str(work / "net.csv"),
           "SIH146_PORT": str(PORT), "SIH146_MONITOR_INTERVAL": "2", "SIH146_REAL_BITCOIN_ENABLED": "false"}
    py = sys.executable

    def start() -> subprocess.Popen:
        p = subprocess.Popen([py, "-m", "backend"], cwd=ROOT, env=env, stdout=open(work / "server.log", "a"), stderr=subprocess.STDOUT)
        for _ in range(120):
            try:
                call("GET", "/api/health")
                return p
            except Exception:
                time.sleep(1)
        raise SystemExit("FAILED: server did not start")

    say(f"scratch database in {work}")
    server = start()
    try:
        # ---- 1. import and first analysis -------------------------------------------------------------------------
        check(get("/api/health")["mode"] == "local", "server is up in local mode")
        s, r = call("POST", "/api/import/synthetic-csv", {})
        check(s == 200 and r["wallets_total"] == 410, f"synthetic dataset imported ({r['inserted']} transfers, {r['wallets_total']} wallets)")
        s, r = call("POST", "/api/import/synthetic-network", {})
        check(s == 200 and r["observations_total"] > 6000, f"synthetic network observations imported ({r['observations_total']})")
        wait(lambda st: st["last_analysis_run_id"] and not st["analysis_stale"] and not st["analysis_in_progress"], "the monitor's first analysis")
        ov = get("/api/overview")
        check(ov["transactions_total"] == 5000 and ov["wallets_total"] == 410 and ov["cases_total"] == 0, "overview: 5,000 transfers, 410 wallets, no cases exist yet")
        check(ov["anomalous_wallets"] == 41 and ov["leads_total"] == 42 and ov["clusters_total"] > 0, f"analysis produced {ov['anomalous_wallets']} flagged wallets, {ov['leads_total']} leads, {ov['clusters_total']} clusters")

        # ---- 2. the backend reproduces the existing CLI pipeline -----------------------------------------------------
        bulk = {w["wallet_address"]: w for w in get("/api/analysis/wallets")["wallets"]}
        with open(ROOT / "data" / "fusion_results.csv", newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        same = all(bulk[r["wallet_address"]]["ml_prediction"] == r["ml_anomaly_prediction"]
                   and bulk[r["wallet_address"]]["forensic_rule_count"] == int(r["forensic_rule_count"])
                   and abs(bulk[r["wallet_address"]]["combined_score"] - float(r["combined_score"])) < 1e-6 for r in rows)
        check(len(rows) == 410 and same, "all 410 wallets match data/fusion_results.csv from the command-line pipeline (prediction, rules, combined score)")

        # ---- 3. leads, evidence, clusters, graph -----------------------------------------------------------------------
        lead = get("/api/leads", limit=1)["items"][0]
        w = lead["wallet_address"]
        d = get(f"/api/leads/{w}")
        check(lead["priority_rank"] == 1 and d["findings"] and "not a case" in d["disclaimer"].lower(), f"top lead {w}: {len(d['findings'])} rules with explanations; a lead is not a case")
        check(lead["is_case"] is False and get("/api/overview")["cases_total"] == 0, "no case was created by the analysis")
        cl = get("/api/clusters", q=w, method="transaction_community")["items"][0]["cluster_id"]
        g = get("/api/graph", wallet=w, depth=1, node_types="wallet,device,ip_observation", max_nodes=20)
        check({n["type"] for n in g["nodes"]} >= {"wallet", "device", "ip_observation"}, f"graph for {w}: {g['counts']['nodes']} (IP/device nodes are synthetic)")
        cd = get(f"/api/clusters/{cl}")
        check(cd["wallet_count"] >= 2 and "not" in cd["note"].lower(), f"cluster {cl}: {cd['wallet_count']} wallets, described as connected activity, not ownership")

        # ---- 4. search ---------------------------------------------------------------------------------------------------
        tx = get(f"/api/wallets/{w}/transactions", limit=1)["items"][0]["transaction_id"]
        obs = get("/api/network/observations", wallet=w, limit=1)["items"][0]
        for term, cat in ((w, "wallets"), (tx, "transactions"), (obs["ip_address"], "ip_observations"), (obs["device_id"], "devices"), (cl, "clusters")):
            res = get("/api/search", q=term)
            check(res["categories"][cat]["total"] >= 1, f"search '{term}' finds it under {cat}")

        # ---- 5. the investigator's decisions -------------------------------------------------------------------------------
        s, r = call("PUT", f"/api/wallets/{w}/review", {"status": "Under Review"})
        check(s == 200 and r["status"] == "Under Review", "marked under review (a marker, not a case)")
        s, case = call("POST", "/api/cases", {"title": f"Fan-in review of {w}", "priority": "High", "note": "Opened from the top lead.",
                                              "items": [{"type": "lead", "id": w}, {"type": "cluster", "id": cl}, {"type": "transaction", "id": tx}]})
        cid = case["case_id"]
        check(s == 201 and case["item_counts"] == {"lead": 1, "wallet": 0, "transaction": 1, "cluster": 1}, f"{cid} created by the investigator with a lead, a cluster and a transaction")
        snap = next(i for i in case["items"] if i["item_type"] == "lead")["evidence_snapshot"]
        call("PATCH", f"/api/cases/{cid}", {"status": "Under investigation"})
        call("POST", f"/api/cases/{cid}/notes", {"text": "Checked the repeated counterparties."})
        call("POST", f"/api/cases/{cid}/reviews", {"item_type": "lead", "item_id": w})
        hist = [h["action"] for h in get(f"/api/cases/{cid}/history")]
        check(hist[0] == "case_created" and {"note_added", "evidence_reviewed", "status_changed"} <= set(hist), f"case history recorded: {', '.join(hist)}")
        check(get(f"/api/leads/{w}")["review_status"] == "Case Created", "the lead now shows 'Case Created' (derived from case membership)")

        # ---- 6. new data arrives -> automatic re-analysis; the saved evidence does not move --------------------------------
        before = get("/api/monitor/status")
        call("PUT", "/api/settings", {"stream_enabled": True, "stream_rate_per_minute": 300})
        wait(lambda st: st["transactions_total"] > before["transactions_total"] and st["last_analysis_run_id"] > before["last_analysis_run_id"]
             and not st["analysis_in_progress"], "stream transactions and the automatic re-analysis")
        call("PUT", "/api/settings", {"stream_enabled": False})
        after = get("/api/monitor/status")
        check(after["data_source"]["is_real_data"] is False and "Synthetic" in after["data_source"]["label"], f"monitoring source is labelled '{after['data_source']['label']}' and not real data")
        check(after["transactions_total"] > 5000, f"{after['transactions_total'] - 5000} synthetic stream transactions arrived and were analysed automatically (run #{after['last_analysis_run_id']})")
        later = get(f"/api/cases/{cid}")
        snap2 = next(i for i in later["items"] if i["item_type"] == "lead")["evidence_snapshot"]
        check(snap2["run_id"] == snap["run_id"] and snap2["combined_score"] == snap["combined_score"], "the evidence saved in the case did not change after the re-analysis")

        # ---- 7. persistence across a restart ---------------------------------------------------------------------------------
        call("PUT", "/api/settings", {"interval_seconds": 3})
        server.terminate()
        server.wait(timeout=30)
        say("server stopped; starting it again on the same database")
        server = start()
        again = get(f"/api/cases/{cid}")
        check(again["title"] == case["title"] and len(again["history"]) == len(later["history"]), "after a restart the case, its evidence and its history are still there")
        check(get("/api/settings")["settings"]["interval_seconds"] == 3, "saved monitor settings survived the restart")
        check(get(f"/api/wallets/{w}/review")["status"] == "Case Created", "review status survived the restart")

        # ---- 8. the optional real-data source stays off --------------------------------------------------------------------------
        src = get("/api/sources/real-bitcoin")
        check(src["status"] == "not_configured" and src["stored"]["transactions"] == 0, "real Bitcoin source: adapter ready, external source not configured, nothing stored")
        s, r = call("POST", "/api/sources/real-bitcoin/fetch", {})
        check(s == 409 and r["error"]["code"] == "source_not_enabled", "a fetch is refused while the source is disabled (no network access attempted)")
        check(set(get("/api/overview")["by_source"]) == {"synthetic"}, "the database holds synthetic data only")
        print("\nALL CHECKS PASSED", flush=True)
    finally:
        server.terminate()
        try:
            server.wait(timeout=30)
        except Exception:
            server.kill()


if __name__ == "__main__":
    main()
