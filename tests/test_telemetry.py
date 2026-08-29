"""Unit tests for the optional telemetry setup -- no AWS dependency, no
real OpenTelemetry exporter construction, same mocking style as the
other tool tests in this repo (temp DBs / mocked requests.post).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import tools.telemetry as telemetry  # noqa: E402


def test_configure_telemetry_is_noop_without_env_vars(monkeypatch):
    monkeypatch.delenv("STRANDS_TELEMETRY_CONSOLE", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    constructed = []
    monkeypatch.setattr(telemetry, "StrandsTelemetry", lambda: constructed.append(True))

    telemetry.configure_telemetry()

    assert constructed == []


def test_configure_telemetry_sets_up_console_exporter_when_enabled(monkeypatch):
    monkeypatch.setenv("STRANDS_TELEMETRY_CONSOLE", "1")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    class FakeTelemetry:
        def __init__(self):
            self.console_called = False
            self.otlp_called = False

        def setup_console_exporter(self):
            self.console_called = True
            return self

        def setup_otlp_exporter(self):
            self.otlp_called = True
            return self

    fake = FakeTelemetry()
    monkeypatch.setattr(telemetry, "StrandsTelemetry", lambda: fake)

    telemetry.configure_telemetry()

    assert fake.console_called is True
    assert fake.otlp_called is False


def test_configure_telemetry_sets_up_otlp_exporter_when_enabled(monkeypatch):
    monkeypatch.delenv("STRANDS_TELEMETRY_CONSOLE", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    class FakeTelemetry:
        def __init__(self):
            self.otlp_called = False

        def setup_otlp_exporter(self):
            self.otlp_called = True
            return self

    fake = FakeTelemetry()
    monkeypatch.setattr(telemetry, "StrandsTelemetry", lambda: fake)

    telemetry.configure_telemetry()

    assert fake.otlp_called is True


def test_print_metrics_silent_when_console_disabled(monkeypatch, capsys):
    monkeypatch.delenv("STRANDS_TELEMETRY_CONSOLE", raising=False)

    telemetry.print_metrics(result=object())  # would blow up on result.metrics if it tried

    assert capsys.readouterr().out == ""


def test_print_metrics_prints_when_console_enabled(monkeypatch, capsys):
    monkeypatch.setenv("STRANDS_TELEMETRY_CONSOLE", "1")
    monkeypatch.setattr(telemetry, "metrics_to_string", lambda metrics: "fake-metrics-summary")

    class FakeResult:
        metrics = object()

    telemetry.print_metrics(FakeResult())

    assert "fake-metrics-summary" in capsys.readouterr().out
