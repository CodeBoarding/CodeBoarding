"""The catalogue: one table over the vocabularies a repository uses to name the same thing (§7).

An image (`openzipkin/zipkin`), an Aspire constructor (`AddRedis`), a URL scheme (`amqp://`), a driver
(`Npgsql`) and a client type (`VectorStore`) all reach one row, and the row carries both the kind and
the name a reader sees, so the two can never disagree. §12 records that this module is the table's
home: a kind is never decided anywhere else.
"""

from __future__ import annotations

import re

from static_analyzer.wiring.units import alias_key
from static_analyzer.wiring_results import RESOURCE_PREFIX, ResourceKind

#: Keys are the bare word each vocabulary reduces to (`word_of`).
CATALOGUE: dict[str, tuple[ResourceKind, str]] = {
    "postgres": (ResourceKind.DB, "PostgreSQL"),
    "postgresql": (ResourceKind.DB, "PostgreSQL"),
    "postgis": (ResourceKind.DB, "PostgreSQL"),
    "pgvector": (ResourceKind.DB, "PostgreSQL"),
    "timescaledb": (ResourceKind.DB, "TimescaleDB"),
    "mysql": (ResourceKind.DB, "MySQL"),
    "mariadb": (ResourceKind.DB, "MariaDB"),
    "mssql": (ResourceKind.DB, "SQL Server"),
    "sqlserver": (ResourceKind.DB, "SQL Server"),
    "mongo": (ResourceKind.DB, "MongoDB"),
    "mongodb": (ResourceKind.DB, "MongoDB"),
    "cassandra": (ResourceKind.DB, "Cassandra"),
    "scylla": (ResourceKind.DB, "ScyllaDB"),
    "cockroach": (ResourceKind.DB, "CockroachDB"),
    "cockroachdb": (ResourceKind.DB, "CockroachDB"),
    "couchdb": (ResourceKind.DB, "CouchDB"),
    "couchbase": (ResourceKind.DB, "Couchbase"),
    "oracle": (ResourceKind.DB, "Oracle"),
    "hsqldb": (ResourceKind.DB, "HSQLDB"),
    "sqlite": (ResourceKind.DB, "SQLite"),
    "clickhouse": (ResourceKind.DB, "ClickHouse"),
    "neo4j": (ResourceKind.DB, "Neo4j"),
    "cosmos": (ResourceKind.DB, "Azure Cosmos DB"),
    "qdrant": (ResourceKind.DB, "Qdrant"),
    "weaviate": (ResourceKind.DB, "Weaviate"),
    "milvus": (ResourceKind.DB, "Milvus"),
    "chroma": (ResourceKind.DB, "Chroma"),
    "vectorstore": (ResourceKind.DB, "Vector store"),
    "jdbc": (ResourceKind.DB, "SQL database"),
    "redis": (ResourceKind.CACHE, "Redis"),
    "valkey": (ResourceKind.CACHE, "Valkey"),
    "dragonfly": (ResourceKind.CACHE, "Dragonfly"),
    "keydb": (ResourceKind.CACHE, "KeyDB"),
    "memcached": (ResourceKind.CACHE, "Memcached"),
    "connectionmultiplexer": (ResourceKind.CACHE, "Redis"),
    "minio": (ResourceKind.STORE, "MinIO"),
    "s3": (ResourceKind.STORE, "S3"),
    "azurite": (ResourceKind.STORE, "Azure Storage"),
    "blob": (ResourceKind.STORE, "Azure Blob Storage"),
    "fakegcs": (ResourceKind.STORE, "Cloud Storage"),
    "etcd": (ResourceKind.STORE, "etcd"),
    "git": (ResourceKind.STORE, "Git repository"),
    "elasticsearch": (ResourceKind.STORE, "Elasticsearch"),
    "opensearch": (ResourceKind.STORE, "OpenSearch"),
    "rabbitmq": (ResourceKind.BROKER, "RabbitMQ"),
    "rabbit": (ResourceKind.BROKER, "RabbitMQ"),
    "amqp": (ResourceKind.BROKER, "AMQP broker"),
    "kafka": (ResourceKind.BROKER, "Kafka"),
    "redpanda": (ResourceKind.BROKER, "Redpanda"),
    "nats": (ResourceKind.BROKER, "NATS"),
    "pulsar": (ResourceKind.BROKER, "Pulsar"),
    "activemq": (ResourceKind.BROKER, "ActiveMQ"),
    "artemis": (ResourceKind.BROKER, "ActiveMQ Artemis"),
    "zookeeper": (ResourceKind.BROKER, "ZooKeeper"),
    "azureservicebus": (ResourceKind.BROKER, "Azure Service Bus"),
    "servicebus": (ResourceKind.BROKER, "Azure Service Bus"),
    "mosquitto": (ResourceKind.BROKER, "Mosquitto"),
    "nginx": (ResourceKind.GATEWAY, "nginx"),
    "envoy": (ResourceKind.GATEWAY, "Envoy"),
    "traefik": (ResourceKind.GATEWAY, "Traefik"),
    "haproxy": (ResourceKind.GATEWAY, "HAProxy"),
    "kong": (ResourceKind.GATEWAY, "Kong"),
    "caddy": (ResourceKind.GATEWAY, "Caddy"),
    "yarp": (ResourceKind.GATEWAY, "YARP"),
    "zipkin": (ResourceKind.API, "Zipkin"),
    "jaeger": (ResourceKind.API, "Jaeger"),
    "prometheus": (ResourceKind.API, "Prometheus"),
    "grafana": (ResourceKind.API, "Grafana"),
    "opentelemetry": (ResourceKind.API, "OpenTelemetry"),
    "otel": (ResourceKind.API, "OpenTelemetry"),
    "otlp": (ResourceKind.API, "OpenTelemetry"),
    "kibana": (ResourceKind.API, "Kibana"),
    "logstash": (ResourceKind.API, "Logstash"),
    "fluentd": (ResourceKind.API, "Fluentd"),
    "loki": (ResourceKind.API, "Loki"),
    "tempo": (ResourceKind.API, "Tempo"),
    "seq": (ResourceKind.API, "Seq"),
    "temporal": (ResourceKind.API, "Temporal"),
    "keycloak": (ResourceKind.API, "Keycloak"),
    "vault": (ResourceKind.API, "Vault"),
    "ollama": (ResourceKind.API, "Ollama"),
    "openai": (ResourceKind.API, "OpenAI"),
    "azureopenai": (ResourceKind.API, "Azure OpenAI"),
    "foundry": (ResourceKind.API, "Azure AI Foundry"),
    "anthropic": (ResourceKind.API, "Anthropic"),
    "bedrock": (ResourceKind.API, "Bedrock"),
    "sentry": (ResourceKind.API, "Sentry"),
    "sendgrid": (ResourceKind.API, "SendGrid"),
    "twilio": (ResourceKind.API, "Twilio"),
    "stripe": (ResourceKind.API, "Stripe"),
    "mailhog": (ResourceKind.API, "MailHog"),
    "maildev": (ResourceKind.API, "MailDev"),
    "mailpit": (ResourceKind.API, "Mailpit"),
    "localstack": (ResourceKind.API, "LocalStack"),
}

#: The driver a manifest depends on, by its package name in Maven Central, NuGet or npm, and the
#: catalogue word it names: a `pom.xml` that depends on `hsqldb` talks to an HSQLDB.
DRIVERS: dict[str, str] = {
    "hsqldb": "hsqldb",
    "mysql-connector-j": "mysql",
    "mysql-connector-java": "mysql",
    "mariadb-java-client": "mariadb",
    "postgresql": "postgres",
    "npgsql": "postgres",
    "pg": "postgres",
    "mysql2": "mysql",
    "ioredis": "redis",
    "redis": "redis",
    "jedis": "redis",
    "lettuce-core": "redis",
    "stackexchange.redis": "redis",
    "mongodb": "mongodb",
    "mongodb-driver-sync": "mongodb",
    "mongodb.driver": "mongodb",
    "microsoft.data.sqlclient": "sqlserver",
    "system.data.sqlclient": "sqlserver",
    "rabbitmq.client": "rabbitmq",
    "amqp-client": "rabbitmq",
    "amqplib": "rabbitmq",
    "kafka-clients": "kafka",
    "kafkajs": "kafka",
    "confluent.kafka": "kafka",
    "zipkin-reporter-brave": "zipkin",
    "zipkin-sender-urlconnection": "zipkin",
    "spring-cloud-starter-zipkin": "zipkin",
    "spring-cloud-sleuth-zipkin": "zipkin",
    "spring-boot-starter-zipkin": "zipkin",
    "opentelemetry-exporter-zipkin": "zipkin",
    "opentelemetry-exporter-otlp": "otel",
    "micrometer-registry-otlp": "otel",
}

_VERSIONED = re.compile(r"(?i)[-_.]?(?:v?\d[\w.]*|alpine|slim|latest|bookworm|bullseye|focal|jammy)$")
_LONGEST_FIRST = sorted(CATALOGUE, key=len, reverse=True)


def classify(token: str) -> tuple[ResourceKind | None, str]:
    """What a word says a thing is, and what to call it: the word itself, or the longest catalogue word inside it.

    `bitnami/postgresql-repmgr` and `SimpleVectorStore` carry their word; `openai-key` and
    `vaultwarden` carry one too, which is why a compose service name and a client type are read
    this way and an Aspire constructor is not (`classify_word`).
    """
    word = word_of(token)
    if word in CATALOGUE:
        return CATALOGUE[word]
    for known in _LONGEST_FIRST:
        if known in word:
            return CATALOGUE[known]
    return None, ""


def classify_word(token: str) -> tuple[ResourceKind | None, str]:
    """What a word says when it must be the whole word: an Aspire constructor, a configuration key segment."""
    return CATALOGUE.get(word_of(token), (None, ""))


def classify_image(repository: str) -> tuple[ResourceKind | None, str]:
    """What an image is, from any of its path segments, the last first: `mssql/server` is a SQL Server."""
    for segment in reversed(repository.split("/")):
        kind, display = classify(segment)
        if kind is not None:
            return kind, display
    return None, ""


def word_of(token: str) -> str:
    """A word from an image, a constructor or a scheme, reduced to what the catalogue is keyed on."""
    word = token.strip().lower().rsplit("/", 1)[-1]
    word = _VERSIONED.sub("", word)
    return re.sub(r"[^a-z0-9]", "", word)


def resource_key(kind: ResourceKind, name: str) -> str:
    """`resource:<kind>:<name>`, the name spelled the way a join compares it (§8)."""
    return f"{RESOURCE_PREFIX}{kind.value}:{alias_key(name)}"
