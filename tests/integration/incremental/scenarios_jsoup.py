"""Change scenarios for incremental analysis benchmarking against jsoup.

Each scenario defines a set of deterministic file edits that can be applied
to the jsoup repo (at tag jsoup-1.22.1) to exercise different code paths in
the incremental analysis pipeline.
"""

from tests.integration.incremental.scenarios import ChangeScenario, FileEdit


# ---------------------------------------------------------------------------
# Scenario 1: Cosmetic Javadoc change
# ---------------------------------------------------------------------------
def _cosmetic_javadoc_edit(content: str) -> str:
    """Reword the top-level Javadoc without changing any code logic."""
    return content.replace(
        "The core public access point to the jsoup functionality.",
        "The core public access point to jsoup HTML parsing and DOM manipulation functionality.",
    )


COSMETIC_JAVADOC = ChangeScenario(
    name="cosmetic_javadoc",
    description="Reword the top-level Javadoc in Jsoup.java without changing code logic",
    edits=[
        FileEdit(
            file_path="src/main/java/org/jsoup/Jsoup.java",
            action="modify",
            content_fn=_cosmetic_javadoc_edit,
        ),
    ],
    commit_message="docs: reword Jsoup class Javadoc for clarity",
    expected_outcome="skip",
)


# ---------------------------------------------------------------------------
# Scenario 2: Add utility method (purely additive)
# ---------------------------------------------------------------------------
_NEW_UTILITY_METHOD = """
    /**
     * Check if the given path points to a gzip-compressed file based on its extension.
     *
     * @param path the file path to check
     * @return true if the file extension indicates gzip compression (.gz or .z)
     */
    public static boolean isGzipFile(Path path) {
        String name = path.getFileName().toString().toLowerCase(java.util.Locale.ENGLISH);
        return name.endsWith(".gz") || name.endsWith(".z");
    }

"""


def _add_utility_method(content: str) -> str:
    """Insert a new isGzipFile static method after the private constructor."""
    old = "    private DataUtil() {}\n\n    /**\n     * Loads and parses a file to a Document, with the HtmlParser."
    new = (
        "    private DataUtil() {}\n"
        + _NEW_UTILITY_METHOD
        + "    /**\n     * Loads and parses a file to a Document, with the HtmlParser."
    )
    return content.replace(old, new)


ADD_UTILITY_METHOD = ChangeScenario(
    name="add_utility_method",
    description="Add a new isGzipFile utility method to DataUtil.java",
    edits=[
        FileEdit(
            file_path="src/main/java/org/jsoup/helper/DataUtil.java",
            action="modify",
            content_fn=_add_utility_method,
        ),
    ],
    commit_message="feat: add isGzipFile utility method to DataUtil",
    expected_outcome="skip",
    expected_additive=True,
)


# ---------------------------------------------------------------------------
# Scenario 3: Modify function logic (single file, localized)
# ---------------------------------------------------------------------------
def _modify_set_max_depth(content: str) -> str:
    """Change setMaxDepth to allow maxDepth >= 0, with a conditional for zero."""
    old = (
        "    public Parser setMaxDepth(int maxDepth) {\n"
        '        Validate.isTrue(maxDepth >= 1, "maxDepth must be >= 1");\n'
        "        this.maxDepth = maxDepth;\n"
        "        return this;\n"
        "    }"
    )
    new = (
        "    public Parser setMaxDepth(int maxDepth) {\n"
        '        Validate.isTrue(maxDepth >= 0, "maxDepth must be >= 0");\n'
        "        if (maxDepth == 0) {\n"
        "            maxDepth = Integer.MAX_VALUE; // 0 means unlimited\n"
        "        }\n"
        "        this.maxDepth = maxDepth;\n"
        "        return this;\n"
        "    }"
    )
    return content.replace(old, new)


MODIFY_FUNCTION_LOGIC = ChangeScenario(
    name="modify_function_logic",
    description="Change setMaxDepth in Parser.java to accept maxDepth >= 0 with conditional handling",
    edits=[
        FileEdit(
            file_path="src/main/java/org/jsoup/parser/Parser.java",
            action="modify",
            content_fn=_modify_set_max_depth,
        ),
    ],
    commit_message="feat: allow maxDepth of 0 to mean unlimited in Parser.setMaxDepth",
    expected_outcome="patch",
)


# ---------------------------------------------------------------------------
# Scenario 4: Cross-module parameter addition
# ---------------------------------------------------------------------------
_NEW_SELECT_FIRST_OVERLOAD = """
    /**
     Find the first Element that matches the query, with optional case-insensitive matching.

     @param cssQuery CSS selector
     @param root root element to descend into
     @param caseInsensitive if true, the query matching is case-insensitive
     @return the matching element, or <b>null</b> if none.
     @since 1.22.2
     */
    public static @Nullable Element selectFirst(String cssQuery, Element root, boolean caseInsensitive) {
        Validate.notEmpty(cssQuery);
        if (caseInsensitive) {
            cssQuery = cssQuery.toLowerCase(java.util.Locale.ENGLISH);
        }
        return Collector.findFirst(evaluatorOf(cssQuery), root);
    }

"""


def _add_select_first_overload(content: str) -> str:
    """Add a selectFirst overload with a caseInsensitive parameter."""
    old = "    /**\n" "     Find the first element matching the query, across multiple roots."
    new = _NEW_SELECT_FIRST_OVERLOAD + (
        "    /**\n" "     Find the first element matching the query, across multiple roots."
    )
    return content.replace(old, new)


ADD_PARAMETER_CROSS_MODULE = ChangeScenario(
    name="add_parameter_cross_module",
    description="Add selectFirst overload with caseInsensitive param to Selector.java",
    edits=[
        FileEdit(
            file_path="src/main/java/org/jsoup/select/Selector.java",
            action="modify",
            content_fn=_add_select_first_overload,
        ),
    ],
    commit_message="feat: add case-insensitive selectFirst overload to Selector",
    expected_outcome="patch",
)


# ---------------------------------------------------------------------------
# Scenario 5: Add new file to existing component
# ---------------------------------------------------------------------------
_CACHE_UTIL_CONTENT = """\
package org.jsoup.helper;

import org.jspecify.annotations.Nullable;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * A simple LRU cache utility for reuse across jsoup internals.
 * <p>Thread-safe via synchronized access to the backing map.</p>
 *
 * @param <K> key type
 * @param <V> value type
 */
public final class CacheUtil<K, V> {
    private final Map<K, V> cache;

    /**
     * Create a new LRU cache with the given maximum capacity.
     *
     * @param maxSize the maximum number of entries
     */
    public CacheUtil(int maxSize) {
        this.cache = new LinkedHashMap<K, V>(maxSize, 0.75f, true) {
            @Override
            protected boolean removeEldestEntry(Map.Entry<K, V> eldest) {
                return size() > maxSize;
            }
        };
    }

    /**
     * Get a value from the cache.
     *
     * @param key the key to look up
     * @return the cached value, or {@code null} if not present
     */
    public synchronized @Nullable V get(K key) {
        return cache.get(key);
    }

    /**
     * Put a value into the cache.
     *
     * @param key the key
     * @param value the value
     */
    public synchronized void put(K key, V value) {
        cache.put(key, value);
    }

    /**
     * Remove a value from the cache.
     *
     * @param key the key to remove
     * @return the removed value, or {@code null} if not present
     */
    public synchronized @Nullable V remove(K key) {
        return cache.remove(key);
    }

    /**
     * Clear all entries from the cache.
     */
    public synchronized void clear() {
        cache.clear();
    }

    /**
     * Get the current number of entries in the cache.
     *
     * @return the cache size
     */
    public synchronized int size() {
        return cache.size();
    }
}
"""

ADD_NEW_FILE = ChangeScenario(
    name="add_new_file",
    description="Add a new CacheUtil.java LRU cache utility to the helper package",
    edits=[
        FileEdit(
            file_path="src/main/java/org/jsoup/helper/CacheUtil.java",
            action="create",
            new_content=_CACHE_UTIL_CONTENT,
        ),
    ],
    commit_message="feat: add LRU cache utility class CacheUtil",
    expected_outcome="patch",
)


# ---------------------------------------------------------------------------
# Scenario 6: Delete function
# ---------------------------------------------------------------------------
def _delete_escape_css_identifier(content: str) -> str:
    """Remove the escapeCssIdentifier wrapper method and its Javadoc."""
    start = "\n    /**\n     Given a CSS identifier (such as a tag, ID, or class), escape any CSS special characters"
    end = "        return TokenQueue.escapeCssIdentifier(in);\n    }\n"
    idx_start = content.index(start)
    idx_end = content.index(end) + len(end)
    return content[:idx_start] + content[idx_end:]


DELETE_FUNCTION = ChangeScenario(
    name="delete_function",
    description="Remove the escapeCssIdentifier() wrapper method from Selector.java",
    edits=[
        FileEdit(
            file_path="src/main/java/org/jsoup/select/Selector.java",
            action="modify",
            content_fn=_delete_escape_css_identifier,
        ),
    ],
    commit_message="refactor: remove unused escapeCssIdentifier() wrapper from Selector",
    expected_outcome="patch",
)


# ---------------------------------------------------------------------------
# Scenario 7: Rename across files
# ---------------------------------------------------------------------------
def _rename_utf8_in_datautil(content: str) -> str:
    """Rename UTF_8 to UTF8_CHARSET in DataUtil.java."""
    return content.replace("UTF_8", "UTF8_CHARSET")


def _rename_utf8_in_document(content: str) -> str:
    """Update the reference in Document.java."""
    return content.replace("DataUtil.UTF_8", "DataUtil.UTF8_CHARSET")


def _rename_utf8_in_httpconnection(content: str) -> str:
    """Update the static import in HttpConnection.java."""
    return content.replace(
        "import static org.jsoup.helper.DataUtil.UTF_8;",
        "import static org.jsoup.helper.DataUtil.UTF8_CHARSET;",
    ).replace("UTF_8", "UTF8_CHARSET")


def _rename_utf8_in_entities(content: str) -> str:
    """Update the reference in Entities.java."""
    return content.replace("DataUtil.UTF_8", "DataUtil.UTF8_CHARSET")


def _rename_utf8_in_urlbuilder(content: str) -> str:
    """Update the static import and usages in UrlBuilder.java."""
    return content.replace(
        "import static org.jsoup.helper.DataUtil.UTF_8;",
        "import static org.jsoup.helper.DataUtil.UTF8_CHARSET;",
    ).replace("UTF_8", "UTF8_CHARSET")


RENAME_ACROSS_FILES = ChangeScenario(
    name="rename_across_files",
    description="Rename UTF_8 to UTF8_CHARSET in DataUtil and all referencing files",
    edits=[
        FileEdit(
            file_path="src/main/java/org/jsoup/helper/DataUtil.java",
            action="modify",
            content_fn=_rename_utf8_in_datautil,
        ),
        FileEdit(
            file_path="src/main/java/org/jsoup/nodes/Document.java",
            action="modify",
            content_fn=_rename_utf8_in_document,
        ),
        FileEdit(
            file_path="src/main/java/org/jsoup/helper/HttpConnection.java",
            action="modify",
            content_fn=_rename_utf8_in_httpconnection,
        ),
        FileEdit(
            file_path="src/main/java/org/jsoup/nodes/Entities.java",
            action="modify",
            content_fn=_rename_utf8_in_entities,
        ),
        FileEdit(
            file_path="src/main/java/org/jsoup/helper/UrlBuilder.java",
            action="modify",
            content_fn=_rename_utf8_in_urlbuilder,
        ),
    ],
    commit_message="refactor: rename UTF_8 to UTF8_CHARSET across codebase",
    expected_outcome="patch",
)


# ---------------------------------------------------------------------------
# Scenario 8: Cross-component change (Element + Selector)
# ---------------------------------------------------------------------------
def _add_timeout_to_element_select_first(content: str) -> str:
    """Add a timeout parameter overload to selectFirst in Element.java."""
    old = (
        "    public @Nullable Element selectFirst(String cssQuery) {\n"
        "        return Selector.selectFirst(cssQuery, this);\n"
        "    }"
    )
    new = (
        "    public @Nullable Element selectFirst(String cssQuery) {\n"
        "        return Selector.selectFirst(cssQuery, this);\n"
        "    }\n"
        "\n"
        "    /**\n"
        "     * Find the first Element that matches the {@link Selector} CSS query, with a timeout.\n"
        "     * <p>If the query takes longer than the specified timeout, returns {@code null}.</p>\n"
        "     * @param cssQuery a {@link Selector} CSS-like query\n"
        "     * @param timeoutMillis maximum time in milliseconds to spend searching\n"
        "     * @return the first matching element, or <b>{@code null}</b> if there is no match or timeout is exceeded.\n"
        "     */\n"
        "    public @Nullable Element selectFirst(String cssQuery, long timeoutMillis) {\n"
        "        if (timeoutMillis <= 0) return selectFirst(cssQuery);\n"
        "        long deadline = System.currentTimeMillis() + timeoutMillis;\n"
        "        Element result = Selector.selectFirst(cssQuery, this);\n"
        "        if (System.currentTimeMillis() > deadline) return null;\n"
        "        return result;\n"
        "    }"
    )
    return content.replace(old, new)


def _add_timeout_to_selector_select_first(content: str) -> str:
    """Add a timeout-aware selectFirst overload to Selector.java."""
    old = (
        "    public static @Nullable Element selectFirst(String cssQuery, Element root) {\n"
        "        Validate.notEmpty(cssQuery);\n"
        "        return Collector.findFirst(evaluatorOf(cssQuery), root);\n"
        "    }"
    )
    new = (
        "    public static @Nullable Element selectFirst(String cssQuery, Element root) {\n"
        "        Validate.notEmpty(cssQuery);\n"
        "        return Collector.findFirst(evaluatorOf(cssQuery), root);\n"
        "    }\n"
        "\n"
        "    /**\n"
        "     Find the first Element that matches the query, with a timeout.\n"
        "\n"
        "     @param cssQuery CSS selector\n"
        "     @param root root element to descend into\n"
        "     @param timeoutMillis maximum time in milliseconds to spend searching\n"
        "     @return the matching element, or <b>null</b> if none or timeout exceeded.\n"
        "     @since 1.22.2\n"
        "     */\n"
        "    public static @Nullable Element selectFirst(String cssQuery, Element root, long timeoutMillis) {\n"
        "        Validate.notEmpty(cssQuery);\n"
        "        if (timeoutMillis <= 0) return selectFirst(cssQuery, root);\n"
        "        long deadline = System.currentTimeMillis() + timeoutMillis;\n"
        "        Element result = Collector.findFirst(evaluatorOf(cssQuery), root);\n"
        "        if (System.currentTimeMillis() > deadline) return null;\n"
        "        return result;\n"
        "    }"
    )
    return content.replace(old, new)


CROSS_COMPONENT_CHANGE = ChangeScenario(
    name="cross_component_change",
    description="Add timeout parameter to selectFirst in Element.java and Selector.java",
    edits=[
        FileEdit(
            file_path="src/main/java/org/jsoup/nodes/Element.java",
            action="modify",
            content_fn=_add_timeout_to_element_select_first,
        ),
        FileEdit(
            file_path="src/main/java/org/jsoup/select/Selector.java",
            action="modify",
            content_fn=_add_timeout_to_selector_select_first,
        ),
    ],
    commit_message="feat: add timeout parameter to selectFirst across Element and Selector",
    expected_outcome="reexpand",
)


# ---------------------------------------------------------------------------
# All scenarios, ordered by expected complexity
# ---------------------------------------------------------------------------
JSOUP_SCENARIOS: list[ChangeScenario] = [
    COSMETIC_JAVADOC,
    ADD_UTILITY_METHOD,
    MODIFY_FUNCTION_LOGIC,
    ADD_PARAMETER_CROSS_MODULE,
    ADD_NEW_FILE,
    DELETE_FUNCTION,
    RENAME_ACROSS_FILES,
    CROSS_COMPONENT_CHANGE,
]

JSOUP_SCENARIOS_BY_NAME: dict[str, ChangeScenario] = {s.name: s for s in JSOUP_SCENARIOS}
