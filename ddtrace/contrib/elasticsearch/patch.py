from importlib import import_module

from ddtrace import config
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...constants import SPAN_MEASURED_KEY
from ...ext import SpanTypes
from ...ext import elasticsearch as metadata
from ...ext import http
from ...internal.compat import urldecode
from ...internal.compat import urlencode
from ...internal.compat import urlparse
from ...internal.utils.wrappers import unwrap as _u
from ...pin import Pin
from .quantize import quantize


config._add(
    "elasticsearch",
    {
        "_default_service": "elasticsearch",
    },
)


def _es_modules():
    module_names = (
        "elasticsearch",
        "elasticsearch1",
        "elasticsearch2",
        "elasticsearch5",
        "elasticsearch6",
        "elasticsearch7",
        "elasticsearch8",
    )
    for module_name in module_names:
        try:
            yield import_module(module_name)
        except ImportError:
            pass


# NB: We are patching the default elasticsearch.transport module
def patch():
    for elasticsearch in _es_modules():
        _patch(elasticsearch)


def _determine_transport_module(elasticsearch):
    transport_module = getattr(elasticsearch, "transport", False)
    if not transport_module:
        import elastic_transport

        transport_module = elastic_transport._transport

    return transport_module


def _patch(elasticsearch):
    if getattr(elasticsearch, "_datadog_patch", False):
        return
    setattr(elasticsearch, "_datadog_patch", True)
    transport_module = _determine_transport_module(elasticsearch)
    _w(transport_module, "Transport.perform_request", _get_perform_request(elasticsearch))
    Pin().onto(transport_module.Transport)


def unpatch():
    for elasticsearch in _es_modules():
        _unpatch(elasticsearch)


def _unpatch(elasticsearch):
    if getattr(elasticsearch, "_datadog_patch", False):
        setattr(elasticsearch, "_datadog_patch", False)
        transport_module = _determine_transport_module(elasticsearch)
        _u(transport_module.Transport, "perform_request")


def _parse_elasticsearch8_urlparams(url):
    parsed_url = urlparse(url)
    url = parsed_url.path
    query_params = parsed_url.query
    params = {}
    if query_params:
        params_list = query_params.split("&")

        for param in params_list:
            kv = param.split("=")
            if len(kv) > 1:
                k, v = kv
                if k:
                    params[k] = urldecode(v)
            elif len(kv) == 1:
                k = kv
                params[k] = ""
    return url, params


def _get_perform_request(elasticsearch):
    def _perform_request(func, instance, args, kwargs):
        pin = Pin.get_from(instance)
        if not pin or not pin.enabled():
            return func(*args, **kwargs)

        with pin.tracer.trace(
            "elasticsearch.query", service=ext_service(pin, config.elasticsearch), span_type=SpanTypes.ELASTICSEARCH
        ) as span:
            span.set_tag(SPAN_MEASURED_KEY)

            # Don't instrument if the trace is not sampled
            if not span.sampled:
                return func(*args, **kwargs)

            method, url = args
            if elasticsearch.__version__ >= (8, 0, 0):
                url, params = _parse_elasticsearch8_urlparams(url)
            else:
                params = kwargs.get("params") or {}

            encoded_params = urlencode(params)
            body = kwargs.get("body")

            span.set_tag(metadata.METHOD, method)
            span.set_tag(metadata.URL, url)
            span.set_tag(metadata.PARAMS, encoded_params)
            if config.elasticsearch.trace_query_string:
                span.set_tag(http.QUERY_STRING, encoded_params)

            if method in ["GET", "POST"]:
                if elasticsearch.__version__ >= (8, 0, 0):
                    span.set_tag(metadata.BODY, instance.serializers.default_serializer.dumps(body).decode("utf-8"))
                else:
                    span.set_tag(metadata.BODY, instance.serializer.dumps(body))

            status = None

            # set analytics sample rate
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, config.elasticsearch.get_analytics_sample_rate())

            span = quantize(span)

            try:
                result = func(*args, **kwargs)
            except elasticsearch.exceptions.TransportError as e:
                span.set_tag(http.STATUS_CODE, getattr(e, "status_code", 500))
                span.error = 1
                raise

            try:
                # Optional metadata extraction with soft fail.
                if isinstance(result, tuple) and len(result) == 2:
                    # elasticsearch<2.4; it returns both the status and the body
                    status, data = result
                else:
                    # elasticsearch>=2.4; internal change for ``Transport.perform_request``
                    # that just returns the body
                    data = result

                took = data.get("took")
                if elasticsearch.__version__ >= (8, 0, 0):
                    if took is not None:
                        span.set_metric(metadata.TOOK, int(took))
                else:
                    if took:
                        span.set_metric(metadata.TOOK, int(took))
            except Exception:
                pass

            if status:
                span.set_tag(http.STATUS_CODE, status)

            return result

    return _perform_request
