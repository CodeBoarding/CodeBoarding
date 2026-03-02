# --- Static Analysis Expected ---
#
# Defined entities:
#   Functions (1): main
#
# Expected call edges (from main() body — 18 calls total):
#   Constructors:  Cat(), Duck(), Config()
#   Services:      create_dog(), process_animals(), build_profile(),
#                  compute(), make_multiplier(), apply_config()
#   Pipeline:      dispatch(), build_query(), get_profile_city(),
#                  transform_list(), create_and_summarize(), pipeline(),
#                  compute_nested(), conditional_dispatch()
#   Closure call:  doubler() — calling return value of make_multiplier()
#   Method call:   profile.get_summary()
#
# Corner cases: cross-module calls spanning core.base, core.services,
#   core.pipeline, core.models; calling a closure stored in a local variable
# Package: main | imports: core
# ---
from core.base import Dog, Cat, Duck
from core.services import create_dog, process_animals, build_profile, compute, make_multiplier, apply_config
from core.models import Config
from core.pipeline import (
    dispatch,
    build_query,
    get_profile_city,
    transform_list,
    create_and_summarize,
    pipeline,
    compute_nested,
    conditional_dispatch,
)


def main():
    dog = create_dog("Rex")
    cat = Cat("Whiskers")
    duck = Duck("Donald")

    animals = [dog, cat, duck]
    sounds = process_animals(animals)

    profile = build_profile("Alice", "alice@example.com", "123 Main St", "Springfield")
    summary = profile.get_summary()

    result = compute(10, 20)

    doubler = make_multiplier(2)
    doubled = doubler(result)

    config = Config(debug=True, tags=["test"])
    settings = apply_config(config)

    # Dict dispatch
    dog_label = dispatch(dog)
    cat_label = dispatch(cat)
    duck_label = dispatch(duck)

    # Method chaining
    query = build_query()

    # Chained attribute calls
    city = get_profile_city("Bob", "bob@example.com", "456 Oak Ave", "Shelbyville")

    # map/filter with callables
    transformed = transform_list([1, -2, 3, -4, 5])

    # *args/**kwargs forwarding
    forwarded_summary = create_and_summarize("Charlie", "charlie@example.com")

    # Generator pipeline
    names = pipeline()

    # Nested function calls
    nested_result = compute_nested()

    # Conditional call chains
    verbose_label = conditional_dispatch(dog, verbose=True)

    return sounds, summary, doubled, settings, query, city, transformed, names, nested_result, verbose_label


if __name__ == "__main__":
    main()
