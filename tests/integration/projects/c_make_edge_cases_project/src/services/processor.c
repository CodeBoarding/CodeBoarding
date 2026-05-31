#include "services/processor.h"

#include <stdio.h>
#include <string.h>

bool services_processor_process(const Entity *entity, char *out, size_t out_len) {
    char description[96];
    if (!models_entities_describe(entity, description, sizeof description)) {
        return false;
    }
    int written = snprintf(out, out_len, "processed:%s", description);
    return written > 0 && (size_t)written < out_len;
}

size_t services_processor_process_batch(const Entity *entities, size_t count, char outputs[][128]) {
    size_t produced = 0;
    for (size_t i = 0; i < count; ++i) {
        if (services_processor_process(&entities[i], outputs[produced], 128)) {
            ++produced;
        }
    }
    return produced;
}
