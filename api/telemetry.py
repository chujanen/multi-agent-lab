"""Optional Azure Monitor tracing for the API: on when APPLICATIONINSIGHTS_CONNECTION_STRING is set."""

from opentelemetry import trace


def setup(app, connection_string):
    if not connection_string:
        return
    from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": "lab-api"}))
    provider.add_span_processor(BatchSpanProcessor(AzureMonitorTraceExporter(connection_string=connection_string)))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()  # outbound call to the Orchestrator carries traceparent


def tag_run(run_id, test):
    """Stamp the current request span so a run's trace can be found by its ID."""
    span = trace.get_current_span()
    span.set_attribute("lab.run_id", run_id)
    span.set_attribute("lab.test", test)


def current_trace_id():
    """32-hex W3C trace id of the active span, or None when tracing is off."""
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.trace_id else None
