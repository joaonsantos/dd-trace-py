import datetime
from importlib import import_module

from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.elasticsearch.patch import patch
from ddtrace.contrib.elasticsearch.patch import unpatch
from ddtrace.ext import http
from tests.utils import TracerTestCase

from ..config import ELASTICSEARCH_CONFIG


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
        elasticsearch = import_module(module_name)
        break
    except ImportError:
        pass
else:
    raise ImportError("could not import any of {0!r}".format(module_names))


class ElasticsearchPatchTest(TracerTestCase):
    """
    Elasticsearch integration test suite.
    Need a running ElasticSearch.
    Test cases with patching.
    Will merge when patching will be the default/only way.
    """

    ES_INDEX = "ddtrace_index"
    ES_TYPE = "ddtrace_type"

    TEST_PORT = str(ELASTICSEARCH_CONFIG["port"])

    def setUp(self):
        """Prepare ES"""
        super(ElasticsearchPatchTest, self).setUp()

        if elasticsearch.__version__ >= (8, 0, 0):
            es = elasticsearch.Elasticsearch(hosts="http://localhost:" + self.TEST_PORT)
        else:
            es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG["port"])
        Pin(tracer=self.tracer).onto(es.transport)

        properties = {"properties": {"created": {"type": "date", "format": "yyyy-MM-dd"}}}
        if elasticsearch.__version__ >= (8, 0, 0):
            properties["properties"]["name"] = {"type": "keyword"}
            es.options(ignore_status=400).indices.create(index=self.ES_INDEX, mappings=properties)
        else:
            mapping = {"mapping": properties}
            es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

        patch()

        self.es = es

    def tearDown(self):
        """Clean ES"""
        super(ElasticsearchPatchTest, self).tearDown()

        unpatch()
        if elasticsearch.__version__ >= (8, 0, 0):
            self.es.options(ignore_status=[400, 404]).indices.delete(index=self.ES_INDEX)
        else:
            self.es.indices.delete(index=self.ES_INDEX, ignore=[400, 404])

    def test_elasticsearch(self):
        es = self.es
        properties = {"properties": {"created": {"type": "date", "format": "yyyy-MM-dd"}}}
        if elasticsearch.__version__ >= (8, 0, 0):
            properties["properties"]["name"] = {"type": "keyword"}
            es.options(ignore_status=400).indices.create(index=self.ES_INDEX, mappings=properties)
        else:
            mapping = {"mapping": properties}
            es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]
        TracerTestCase.assert_is_measured(span)
        assert span.service == "elasticsearch"
        assert span.name == "elasticsearch.query"
        assert span.span_type == "elasticsearch"
        assert span.error == 0
        assert span.get_tag("elasticsearch.method") == "PUT"
        assert span.get_tag("elasticsearch.url") == "/%s" % self.ES_INDEX
        assert span.resource == "PUT /%s" % self.ES_INDEX

        args = {"index": self.ES_INDEX, "doc_type": self.ES_TYPE}
        args["doc_type"] = self.ES_TYPE

        if elasticsearch.__version__ >= (8, 0, 0):
            del args["doc_type"]
            es.index(id=10, document={"name": "ten", "created": datetime.date(2016, 1, 1)}, **args)
            es.index(id=11, document={"name": "eleven", "created": datetime.date(2016, 2, 1)}, **args)
            es.index(id=12, document={"name": "twelve", "created": datetime.date(2016, 3, 1)}, **args)
        else:
            es.index(id=10, body={"name": "ten", "created": datetime.date(2016, 1, 1)}, **args)
            es.index(id=11, body={"name": "eleven", "created": datetime.date(2016, 2, 1)}, **args)
            es.index(id=12, body={"name": "twelve", "created": datetime.date(2016, 3, 1)}, **args)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 3
        span = spans[0]
        TracerTestCase.assert_is_measured(span)
        assert span.error == 0
        if elasticsearch.__version__ >= (8, 0, 0):
            assert span.get_tag("elasticsearch.method") == "PUT"
            assert span.resource == "PUT /%s/_doc/?" % (self.ES_INDEX)
            assert span.get_tag("elasticsearch.url") == "/%s/_doc/%s" % (self.ES_INDEX, 10)
        elif (7, 0, 0) <= elasticsearch.__version__ < (7, 5, 0):
            assert span.get_tag("elasticsearch.method") == "POST"
            assert span.resource == "POST /%s/%s/?" % (self.ES_INDEX, self.ES_TYPE)
            assert span.get_tag("elasticsearch.url") == "/%s/%s/%s" % (self.ES_INDEX, self.ES_TYPE, 10)
        else:
            assert span.get_tag("elasticsearch.method") == "PUT"
            assert span.resource == "PUT /%s/%s/?" % (self.ES_INDEX, self.ES_TYPE)
            assert span.get_tag("elasticsearch.url") == "/%s/%s/%s" % (self.ES_INDEX, self.ES_TYPE, 10)

        es.indices.refresh(index=self.ES_INDEX)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]
        TracerTestCase.assert_is_measured(span)
        assert span.resource == "POST /%s/_refresh" % self.ES_INDEX
        assert span.get_tag("elasticsearch.method") == "POST"
        assert span.get_tag("elasticsearch.url") == "/%s/_refresh" % self.ES_INDEX

        # search data
        args = {"index": self.ES_INDEX, "doc_type": self.ES_TYPE}
        with self.override_http_config("elasticsearch", dict(trace_query_string=True)):
            if elasticsearch.__version__ >= (8, 0, 0):
                del args["doc_type"]
                es.index(id=10, document={"name": "ten", "created": datetime.date(2016, 1, 1)}, **args)
                es.index(id=11, document={"name": "eleven", "created": datetime.date(2016, 2, 1)}, **args)
                es.index(id=12, document={"name": "twelve", "created": datetime.date(2016, 3, 1)}, **args)
                result = es.search(sort=["name:desc"], size=100, query={"match_all": {}}, **args)
            else:
                es.index(id=10, body={"name": "ten", "created": datetime.date(2016, 1, 1)}, **args)
                es.index(id=11, body={"name": "eleven", "created": datetime.date(2016, 2, 1)}, **args)
                es.index(id=12, body={"name": "twelve", "created": datetime.date(2016, 3, 1)}, **args)
                if (7, 0, 0) <= elasticsearch.__version__ < (7, 2, 0):
                    del args["doc_type"]
                result = es.search(sort=["name:desc"], size=100, body={"query": {"match_all": {}}}, **args)

        assert len(result["hits"]["hits"]) == 3, result
        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 4
        span = spans[-1]
        TracerTestCase.assert_is_measured(span)
        method, url = span.resource.split(" ")
        assert method == span.get_tag("elasticsearch.method")
        assert method in ["GET", "POST"]
        assert self.ES_INDEX in url
        if elasticsearch.__version__ >= (8, 0, 0):
            assert span.get_tag("elasticsearch.body").replace(" ", "") == '{"query":{"match_all":{}},"size":100}'
        else:
            assert span.get_tag("elasticsearch.body").replace(" ", "") == '{"query":{"match_all":{}}}'

        assert url.endswith("/_search")
        assert url == span.get_tag("elasticsearch.url")
        if elasticsearch.__version__ >= (8, 0, 0):
            assert set(span.get_tag("elasticsearch.params").split("&")) == {"sort=name%3Adesc"}
            assert set(span.get_tag(http.QUERY_STRING).split("&")) == {"sort=name%3Adesc"}
            self.assertTrue(span.get_metric("elasticsearch.took") >= 0)
        else:
            assert set(span.get_tag("elasticsearch.params").split("&")) == {"sort=name%3Adesc", "size=100"}
            assert set(span.get_tag(http.QUERY_STRING).split("&")) == {"sort=name%3Adesc", "size=100"}
            self.assertTrue(span.get_metric("elasticsearch.took") > 0)

        # Search by type not supported by default json encoder
        query = {"range": {"created": {"gte": datetime.date(2016, 2, 1)}}}
        if elasticsearch.__version__ >= (8, 0, 0):
            result = es.search(size=100, query=query, **args)
        else:
            result = es.search(size=100, body={"query": query}, **args)

        assert len(result["hits"]["hits"]) == 2, result

    def test_analytics_default(self):
        es = self.es
        properties = {"properties": {"created": {"type": "date", "format": "yyyy-MM-dd"}}}
        if elasticsearch.__version__ >= (8, 0, 0):
            properties["properties"]["name"] = {"type": "keyword"}
            es.options(ignore_status=400).indices.create(index=self.ES_INDEX, mappings=properties)
        else:
            mapping = {"mapping": properties}
            es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        self.assertIsNone(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_with_rate(self):
        with self.override_config("elasticsearch", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            es = self.es
            properties = {"properties": {"created": {"type": "date", "format": "yyyy-MM-dd"}}}
            if elasticsearch.__version__ >= (8, 0, 0):
                properties["properties"]["name"] = {"type": "keyword"}
                es.options(ignore_status=400).indices.create(index=self.ES_INDEX, mappings=properties)
            else:
                mapping = {"mapping": properties}
                es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

            spans = self.get_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_without_rate(self):
        with self.override_config("elasticsearch", dict(analytics_enabled=True)):
            es = self.es
            properties = {"properties": {"created": {"type": "date", "format": "yyyy-MM-dd"}}}
            if elasticsearch.__version__ >= (8, 0, 0):
                properties["properties"]["name"] = {"type": "keyword"}
                es.options(ignore_status=400).indices.create(index=self.ES_INDEX, mappings=properties)
            else:
                mapping = {"mapping": properties}
                es.indices.create(index=self.ES_INDEX, ignore=400, body=mapping)

            spans = self.get_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)

    def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        if elasticsearch.__version__ >= (8, 0, 0):
            es = elasticsearch.Elasticsearch(hosts="http://localhost:" + self.TEST_PORT)
        else:
            es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG["port"])
        Pin(tracer=self.tracer).onto(es.transport)

        # Test index creation
        if elasticsearch.__version__ >= (8, 0, 0):
            es.options(ignore_status=400).indices.create(index=self.ES_INDEX)
        else:
            es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        self.reset()
        unpatch()

        if elasticsearch.__version__ >= (8, 0, 0):
            es = elasticsearch.Elasticsearch(hosts="http://localhost:" + self.TEST_PORT)
        else:
            es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG["port"])

        # Test index creation
        if elasticsearch.__version__ >= (8, 0, 0):
            es.options(ignore_status=400).indices.create(index=self.ES_INDEX)
        else:
            es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = self.get_spans()
        self.reset()
        assert not spans, spans

        # Test patch again
        self.reset()
        patch()

        if elasticsearch.__version__ >= (8, 0, 0):
            es = elasticsearch.Elasticsearch(hosts="http://localhost:" + self.TEST_PORT)
        else:
            es = elasticsearch.Elasticsearch(port=ELASTICSEARCH_CONFIG["port"])
        Pin(tracer=self.tracer).onto(es.transport)

        # Test index creation
        if elasticsearch.__version__ >= (8, 0, 0):
            es.options(ignore_status=400).indices.create(index=self.ES_INDEX)
        else:
            es.indices.create(index=self.ES_INDEX, ignore=400)

        spans = self.get_spans()
        self.reset()
        assert spans, spans
        assert len(spans) == 1

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service(self):
        """
        When a user specifies a service for the app
            The elasticsearch integration should not use it.
        """
        assert config.service == "mysvc"

        if elasticsearch.__version__ >= (8, 0, 0):
            self.es.options(ignore_status=400).indices.create(index=self.ES_INDEX)
        else:
            self.es.indices.create(index=self.ES_INDEX, ignore=400)
        Pin(service="es", tracer=self.tracer).onto(self.es.transport)
        spans = self.get_spans()
        self.reset()
        assert len(spans) == 1
        assert spans[0].service != "es"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE_MAPPING="elasticsearch:custom-elasticsearch"))
    def test_service_mapping_config(self):
        """
        When a user specifies a service mapping it should override the default
        """
        assert config.elasticsearch.service != "custom-elasticsearch"

        if elasticsearch.__version__ >= (8, 0, 0):
            self.es.options(ignore_status=400).indices.create(index=self.ES_INDEX)
        else:
            self.es.indices.create(index=self.ES_INDEX, ignore=400)
        spans = self.get_spans()
        self.reset()
        assert len(spans) == 1
        assert spans[0].service == "custom-elasticsearch"

    def test_service_name_config_override(self):
        """
        When a user specifies a service mapping it should override the default
        """
        with self.override_config("elasticsearch", dict(service="test_service")):
            if elasticsearch.__version__ >= (8, 0, 0):
                self.es.options(ignore_status=400).indices.create(index=self.ES_INDEX)
            else:
                self.es.indices.create(index=self.ES_INDEX, ignore=400)
            spans = self.get_spans()
            self.reset()
            assert len(spans) == 1
            assert spans[0].service == "test_service"

    def test_none_param(self):
        try:
            if elasticsearch.__version__ >= (8, 0, 0):
                headers = {"Content-Type": "application/json"}
                self.es.transport.perform_request("GET", "/test-index", body="{}", headers=headers)
            else:
                self.es.transport.perform_request("GET", "/test-index", body="{}", params=None)
        except elasticsearch.exceptions.NotFoundError:
            pass
        spans = self.get_spans()
        assert len(spans) == 1
