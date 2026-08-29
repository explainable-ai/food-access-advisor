"""Optional OpenTelemetry tracing/metrics, shared by all three entrypoints.

Off by default -- each agent already prints its own summary (a ranked
recommendation, a recheck tally), and most runs (a hackathon demo, a
quick manual test) don't need spans on top of that. Opt in with either
environment variable:

    STRANDS_TELEMETRY_CONSOLE=1        print each call's spans and
                                        event-loop metrics (latency,
                                        token counts, tool-call counts)
    OTEL_EXPORTER_OTLP_ENDPOINT=<url>  also export spans to an OTLP
                                        collector -- needs
                                        opentelemetry-exporter-otlp-proto-http
                                        installed separately; not in
                                        requirements.txt since it's
                                        unused unless you set this

Verified against the actually-installed strands-agents package
(1.54.0): StrandsTelemetry() only builds a tracer provider -- nothing is
exported anywhere until one of its setup_*_exporter() methods is called.
So configure_telemetry() is a real no-op, not just a quiet one, when
neither variable is set.
"""

import os

from strands.telemetry import StrandsTelemetry, metrics_to_string

_CONSOLE_ENV = "STRANDS_TELEMETRY_CONSOLE"
_OTLP_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"


def configure_telemetry() -> None:
    """Call once at module import time, before any Agent is built."""
    console = os.getenv(_CONSOLE_ENV)
    otlp = os.getenv(_OTLP_ENV)
    if not console and not otlp:
        return
    telemetry = StrandsTelemetry()
    if console:
        telemetry.setup_console_exporter()
    if otlp:
        telemetry.setup_otlp_exporter()


def print_metrics(result) -> None:
    """Print one agent call's event-loop metrics -- only when console
    telemetry is enabled, so a default run's output is unchanged."""
    if os.getenv(_CONSOLE_ENV):
        print(metrics_to_string(result.metrics))
