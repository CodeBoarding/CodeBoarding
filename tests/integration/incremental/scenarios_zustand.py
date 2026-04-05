"""Change scenarios for incremental analysis benchmarking against zustand.

Each scenario defines a set of deterministic file edits that can be applied
to the zustand repo (v5.0.12) to exercise different code paths in the
incremental analysis pipeline.
"""

from tests.integration.incremental.scenarios import ChangeScenario, FileEdit


# ---------------------------------------------------------------------------
# Scenario 1: Cosmetic comment change
# ---------------------------------------------------------------------------
def _cosmetic_comment_edit(content: str) -> str:
    """Reword the TODO comment in setState without changing any code logic."""
    return content.replace(
        "// TODO: Remove type assertion once https://github.com/microsoft/TypeScript/issues/37663 is resolved\n"
        "    // https://github.com/microsoft/TypeScript/issues/37663#issuecomment-759728342",
        "// NOTE: Type assertion required due to TypeScript limitation (microsoft/TypeScript#37663)\n"
        "    // See: https://github.com/microsoft/TypeScript/issues/37663#issuecomment-759728342",
    )


COSMETIC_COMMENT = ChangeScenario(
    name="cosmetic_comment",
    description="Reword the TODO comment about TypeScript #37663 in vanilla.ts without changing code logic",
    edits=[
        FileEdit(
            file_path="src/vanilla.ts",
            action="modify",
            content_fn=_cosmetic_comment_edit,
        ),
    ],
    commit_message="docs: reword TypeScript issue TODO comment for clarity",
    expected_escalation="cosmetic_skip",
)


# ---------------------------------------------------------------------------
# Scenario 2: Add utility function (purely additive)
# ---------------------------------------------------------------------------
_DEEP_EQUAL_FUNCTION = """

export function deepEqual<T>(valueA: T, valueB: T): boolean {
  if (Object.is(valueA, valueB)) {
    return true
  }
  if (
    typeof valueA !== 'object' ||
    valueA === null ||
    typeof valueB !== 'object' ||
    valueB === null
  ) {
    return false
  }
  const keysA = Object.keys(valueA)
  const keysB = Object.keys(valueB)
  if (keysA.length !== keysB.length) {
    return false
  }
  for (const key of keysA) {
    if (
      !Object.prototype.hasOwnProperty.call(valueB, key) ||
      !deepEqual(
        (valueA as Record<string, unknown>)[key],
        (valueB as Record<string, unknown>)[key],
      )
    ) {
      return false
    }
  }
  return true
}
"""


def _add_deep_equal_function(content: str) -> str:
    return content + _DEEP_EQUAL_FUNCTION


ADD_UTILITY_FUNCTION = ChangeScenario(
    name="add_utility_function",
    description="Append a deepEqual utility function to vanilla/shallow.ts",
    edits=[
        FileEdit(
            file_path="src/vanilla/shallow.ts",
            action="modify",
            content_fn=_add_deep_equal_function,
        ),
    ],
    commit_message="feat: add deepEqual utility for recursive object comparison",
    expected_escalation="additive_skip",
    expected_additive=True,
)


# ---------------------------------------------------------------------------
# Scenario 3: Modify function logic (single file, localized)
# ---------------------------------------------------------------------------
def _add_initial_state_fallback(content: str) -> str:
    """Add fallback for initialState in subscribeWithSelector."""
    return content.replace(
        "    const initialState = fn(set, get, api)\n" "    return initialState",
        "    const initialState = fn(set, get, api)\n"
        "    // Fallback: if initializer returns undefined, use current state\n"
        "    return initialState !== undefined ? initialState : api.getState()",
    )


MODIFY_FUNCTION_LOGIC = ChangeScenario(
    name="modify_function_logic",
    description="Add a fallback for undefined initialState in subscribeWithSelector",
    edits=[
        FileEdit(
            file_path="src/middleware/subscribeWithSelector.ts",
            action="modify",
            content_fn=_add_initial_state_fallback,
        ),
    ],
    commit_message="fix: handle undefined return from subscribeWithSelector initializer",
    expected_escalation="none",
)


# ---------------------------------------------------------------------------
# Scenario 4: Cross-module parameter addition
# ---------------------------------------------------------------------------
def _add_strict_param_to_has_iterable_entries(content: str) -> str:
    """Add a strict parameter to hasIterableEntries."""
    return content.replace(
        "const hasIterableEntries = (\n"
        "  value: Iterable<unknown>,\n"
        "): value is Iterable<unknown> & {\n"
        "  entries(): Iterable<[unknown, unknown]>\n"
        "} =>\n"
        "  // HACK: avoid checking entries type\n"
        "  'entries' in value",
        "const hasIterableEntries = (\n"
        "  value: Iterable<unknown>,\n"
        "  strict: boolean = false,\n"
        "): value is Iterable<unknown> & {\n"
        "  entries(): Iterable<[unknown, unknown]>\n"
        "} =>\n"
        "  // HACK: avoid checking entries type\n"
        "  strict\n"
        "    ? 'entries' in value && typeof (value as any).entries === 'function'\n"
        "    : 'entries' in value",
    )


ADD_PARAMETER_CROSS_MODULE = ChangeScenario(
    name="add_parameter_cross_module",
    description="Add a strict parameter to hasIterableEntries in vanilla/shallow.ts",
    edits=[
        FileEdit(
            file_path="src/vanilla/shallow.ts",
            action="modify",
            content_fn=_add_strict_param_to_has_iterable_entries,
        ),
    ],
    commit_message="feat: add strict mode to hasIterableEntries for type-safe entry checking",
    expected_escalation="none",
)


# ---------------------------------------------------------------------------
# Scenario 5: Add new file
# ---------------------------------------------------------------------------
_LOGGER_MIDDLEWARE_CONTENT = """\
import type { StateCreator, StoreMutatorIdentifier } from '../vanilla.ts'

type Logger = <
  T,
  Mps extends [StoreMutatorIdentifier, unknown][] = [],
  Mcs extends [StoreMutatorIdentifier, unknown][] = [],
>(
  initializer: StateCreator<T, Mps, Mcs>,
  options?: LoggerOptions,
) => StateCreator<T, Mps, Mcs>

export interface LoggerOptions {
  name?: string
  enabled?: boolean
  diff?: boolean
}

type LoggerImpl = <T>(
  storeInitializer: StateCreator<T, [], []>,
  options?: LoggerOptions,
) => StateCreator<T, [], []>

const loggerImpl: LoggerImpl =
  (fn, options = {}) =>
  (set, get, api) => {
    const { name = 'store', enabled = true, diff = false } = options

    const loggedSet: typeof set = (...args) => {
      const prevState = get()
      set(...(args as Parameters<typeof set>))
      const nextState = get()
      if (enabled) {
        console.group(`[${name}] state update`)
        if (diff) {
          console.log('prev:', prevState)
          console.log('next:', nextState)
        } else {
          console.log('state:', nextState)
        }
        console.groupEnd()
      }
    }

    return fn(loggedSet, get, api)
  }

export const logger = loggerImpl as unknown as Logger
"""


ADD_NEW_FILE = ChangeScenario(
    name="add_new_file",
    description="Add a logger middleware file at src/middleware/logger.ts",
    edits=[
        FileEdit(
            file_path="src/middleware/logger.ts",
            action="create",
            new_content=_LOGGER_MIDDLEWARE_CONTENT,
        ),
    ],
    commit_message="feat: add logger middleware for state change debugging",
)


# ---------------------------------------------------------------------------
# Scenario 6: Delete function
# ---------------------------------------------------------------------------
def _delete_remove_store_from_tracked_connections(content: str) -> str:
    """Remove the removeStoreFromTrackedConnections function entirely."""
    old = (
        "const removeStoreFromTrackedConnections = (\n"
        "  name: string | undefined,\n"
        "  store: string | undefined,\n"
        ") => {\n"
        "  if (store === undefined) return\n"
        "  const connectionInfo = trackedConnections.get(name)\n"
        "  if (!connectionInfo) return\n"
        "  delete connectionInfo.stores[store]\n"
        "  if (Object.keys(connectionInfo.stores).length === 0) {\n"
        "    trackedConnections.delete(name)\n"
        "  }\n"
        "}\n"
    )
    # Replace with a no-op stub so call sites don't break
    new = (
        "// removeStoreFromTrackedConnections has been removed\n"
        "const removeStoreFromTrackedConnections = (\n"
        "  _name: string | undefined,\n"
        "  _store: string | undefined,\n"
        ") => {}\n"
    )
    return content.replace(old, new)


DELETE_FUNCTION = ChangeScenario(
    name="delete_function",
    description="Remove the removeStoreFromTrackedConnections() function from devtools.ts",
    edits=[
        FileEdit(
            file_path="src/middleware/devtools.ts",
            action="modify",
            content_fn=_delete_remove_store_from_tracked_connections,
        ),
    ],
    commit_message="refactor: remove unused removeStoreFromTrackedConnections function",
    expected_escalation="none",
)


# ---------------------------------------------------------------------------
# Scenario 7: Rename across files
# ---------------------------------------------------------------------------
def _rename_identity_in_react(content: str) -> str:
    """Rename identity to defaultSelector in react.ts."""
    return content.replace(
        "const identity = <T>(arg: T): T => arg",
        "const defaultSelector = <T>(arg: T): T => arg",
    ).replace(
        "  selector: (state: TState) => StateSlice = identity as any,",
        "  selector: (state: TState) => StateSlice = defaultSelector as any,",
    )


def _rename_identity_in_traditional(content: str) -> str:
    """Rename identity to defaultSelector in traditional.ts."""
    return content.replace(
        "const identity = <T>(arg: T): T => arg",
        "const defaultSelector = <T>(arg: T): T => arg",
    ).replace(
        "  selector: (state: TState) => StateSlice = identity as any,",
        "  selector: (state: TState) => StateSlice = defaultSelector as any,",
    )


RENAME_ACROSS_FILES = ChangeScenario(
    name="rename_across_files",
    description="Rename identity to defaultSelector in react.ts and traditional.ts",
    edits=[
        FileEdit(
            file_path="src/react.ts",
            action="modify",
            content_fn=_rename_identity_in_react,
        ),
        FileEdit(
            file_path="src/traditional.ts",
            action="modify",
            content_fn=_rename_identity_in_traditional,
        ),
    ],
    commit_message="refactor: rename identity to defaultSelector for clarity",
    expected_escalation="none",
)


# ---------------------------------------------------------------------------
# Scenario 8: Cross-component change
# ---------------------------------------------------------------------------
def _add_devtools_state_type_to_devtools(content: str) -> str:
    """Add a DevtoolsState type export to devtools.ts."""
    return content.replace(
        "export type NamedSet<T> = WithDevtools<StoreApi<T>>['setState']",
        "export type NamedSet<T> = WithDevtools<StoreApi<T>>['setState']\n"
        "\n"
        "export type DevtoolsState = {\n"
        "  isRecording: boolean\n"
        "  connectionName: string | undefined\n"
        "  storeName: string | undefined\n"
        "}",
    )


def _export_devtools_state_from_middleware(content: str) -> str:
    """Add DevtoolsState to the re-exports in middleware.ts."""
    return content.replace(
        "export {\n"
        "  devtools,\n"
        "  type DevtoolsOptions,\n"
        "  type NamedSet,\n"
        "} from './middleware/devtools.ts'",
        "export {\n"
        "  devtools,\n"
        "  type DevtoolsOptions,\n"
        "  type DevtoolsState,\n"
        "  type NamedSet,\n"
        "} from './middleware/devtools.ts'",
    )


def _import_devtools_state_in_redux(content: str) -> str:
    """Import DevtoolsState in redux.ts for cross-module reference."""
    return content.replace(
        "import type { NamedSet } from './devtools.ts'",
        "import type { DevtoolsState, NamedSet } from './devtools.ts'",
    )


CROSS_COMPONENT_CHANGE = ChangeScenario(
    name="cross_component_change",
    description="Add DevtoolsState type to devtools.ts, export it from middleware.ts, and import it in redux.ts",
    edits=[
        FileEdit(
            file_path="src/middleware/devtools.ts",
            action="modify",
            content_fn=_add_devtools_state_type_to_devtools,
        ),
        FileEdit(
            file_path="src/middleware.ts",
            action="modify",
            content_fn=_export_devtools_state_from_middleware,
        ),
        FileEdit(
            file_path="src/middleware/redux.ts",
            action="modify",
            content_fn=_import_devtools_state_in_redux,
        ),
    ],
    commit_message="feat: add DevtoolsState type across middleware layer",
)


# ---------------------------------------------------------------------------
# All scenarios, ordered by expected complexity
# ---------------------------------------------------------------------------
ZUSTAND_SCENARIOS: list[ChangeScenario] = [
    COSMETIC_COMMENT,
    ADD_UTILITY_FUNCTION,
    MODIFY_FUNCTION_LOGIC,
    ADD_PARAMETER_CROSS_MODULE,
    ADD_NEW_FILE,
    DELETE_FUNCTION,
    RENAME_ACROSS_FILES,
    CROSS_COMPONENT_CHANGE,
]

ZUSTAND_SCENARIOS_BY_NAME: dict[str, ChangeScenario] = {s.name: s for s in ZUSTAND_SCENARIOS}
