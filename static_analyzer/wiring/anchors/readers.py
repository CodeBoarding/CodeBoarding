"""What the code names: the environment key it reads, and the store it holds a client for.

A compose file that writes `SERVICE_URL` and the line of Python that reads `os.environ["SERVICE_URL"]`
are one fact in two files, and this reader records the second half, in every language the engines
support and only where the key is a literal. A client type does the same job for a resource: a
`VectorStore` or a `MongoTemplate` in a file says this unit talks to one.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from static_analyzer.wiring.anchors.keys import Owners, env_key
from static_analyzer.wiring.units import alias_key
from static_analyzer.wiring_results import Anchor, AnchorFamily, AnchorRole

#: One group per notation, because a key read is spelled differently in every language. Only
#: the standard library's and the frameworks' readers: a project's own wrapper is that project's.
ENVIRONMENT_READ = re.compile(
    r"(?:os\.environ(?:\.get)?\s*[\[(]\s*|os\.[Gg]etenv\(\s*|\bgetenv\(\s*|System\.getenv\(\s*|"
    r"GetEnvironmentVariable\(\s*|ENV\.fetch\(\s*|ENV\[\s*)['\"]([A-Za-z_]\w*)['\"]"
    r"|process\.env\.([A-Za-z_]\w*)"
    r"|process\.env\[\s*['\"]([A-Za-z_]\w*)['\"]\s*\]"
    r"|import\.meta\.env\.([A-Za-z_]\w*)"
    r"|Configuration\s*\[\s*\"([\w:.-]+)\"\s*\]"
    r"|(?:GetConnectionString|GetValue<[^>]*>)\s*\(\s*\"([\w:.-]+)\"\s*\)"
    r"|@Value\s*\(\s*\"[^\"]*?\$\{([\w.-]+)"
)

#: A client type whose name says what the code talks to: a store, a broker, a cache, a database.
RESOURCE_CLIENT = re.compile(
    r"\b(\w*VectorStore|MongoTemplate|MongoClient|MongoRepository|RedisTemplate|StringRedisTemplate|RabbitTemplate|"
    r"KafkaTemplate|JdbcTemplate|DataSource|DbContext|S3Client|AmazonS3|MinioClient|ElasticsearchClient|"
    r"CosmosClient|BlobServiceClient|ServiceBusClient|IConnectionMultiplexer|IDistributedCache)\b"
)

_TRIGGERS = ("environ", "getenv", "Getenv", "process.env", "import.meta.env", "Configuration[", "@Value(",
             "GetConnectionString", "GetValue<", "ENV[", "ENV.fetch",
             "Store", "Template", "Client", "DbContext", "DataSource", "Cache")  # fmt: skip


def read(sources: Sequence[tuple[str, str]], owners: Owners) -> list[Anchor]:
    """Every environment key the code asks for by name."""
    found: list[Anchor] = []
    for path, text in sources:
        if not any(trigger in text for trigger in _TRIGGERS):
            continue
        unit = owners.of(path)
        found += _clients(path, text, unit)
        for match in ENVIRONMENT_READ.finditer(text):
            key = next((group for group in match.groups() if group), "")
            if not key:
                continue
            found.append(
                Anchor(
                    family=AnchorFamily.CONFIGURATION,
                    role=AnchorRole.USE,
                    key=key,
                    norm_key=env_key(key),
                    file=path,
                    line=text.count("\n", 0, match.start()) + 1,
                    column=match.start() - text.rfind("\n", 0, match.start()),
                    unit=unit,
                )
            )
    return found


def _clients(path: str, text: str, unit: str) -> list[Anchor]:
    """The stores this file holds a client for, one anchor per kind rather than per mention."""
    found, seen = [], set()
    for match in RESOURCE_CLIENT.finditer(text):
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        found.append(
            Anchor(
                family=AnchorFamily.DATA_ACCESS,
                role=AnchorRole.USE,
                key=name,
                norm_key=alias_key(name),
                file=path,
                line=text.count("\n", 0, match.start()) + 1,
                column=match.start() - text.rfind("\n", 0, match.start()),
                unit=unit,
            )
        )
    return found
