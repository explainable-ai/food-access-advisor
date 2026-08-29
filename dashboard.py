"""Impact dashboard — a small local Flask app for the design canvas's own
success metric: unclosed-gaps count and median days-to-resolution, per
region, not pooled.

Deliberately separate from tools/impact_metrics.py, which does the actual
computation with zero web/UI dependencies — this file is only the
presentation layer, so the metrics themselves stay unit-testable without
Flask installed or running.

Run: python dashboard.py
Then open http://127.0.0.1:5050 in a browser. Auto-refreshes every 30s so
it stays current as the Watchdog writes to the log — no websockets/SSE,
just a meta refresh, since a hackathon demo dashboard doesn't need
anything fancier than that.
"""

from flask import Flask, render_template

from tools.impact_metrics import compute_impact_metrics

app = Flask(__name__)

REFRESH_SECONDS = 30


@app.route("/")
def index():
    metrics = compute_impact_metrics()
    return render_template(
        "dashboard.html", metrics=metrics, refresh_seconds=REFRESH_SECONDS
    )


if __name__ == "__main__":
    print(f"Impact dashboard — http://127.0.0.1:5050 (auto-refreshes every {REFRESH_SECONDS}s)")
    app.run(port=5050, debug=False)
