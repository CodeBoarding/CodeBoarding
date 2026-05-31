#include "models/entities.h"

#include <stdio.h>

void models_entities_init_cat(Entity *entity, const char *name) {
    models_base_init(&entity->base, name, 0);
    entity->kind = ENTITY_KIND_CAT;
}

void models_entities_init_dog(Entity *entity, const char *name) {
    models_base_init(&entity->base, name, 1);
    entity->kind = ENTITY_KIND_DOG;
}

bool models_entities_speak(const Entity *entity, char *out, size_t out_len) {
    const char *sound = (entity->kind == ENTITY_KIND_CAT) ? "meow" : "woof";
    int written = snprintf(out, out_len, "%s", sound);
    return written > 0 && (size_t)written < out_len;
}

bool models_entities_describe(const Entity *entity, char *out, size_t out_len) {
    if (entity->kind == ENTITY_KIND_CAT) {
        int written = snprintf(out, out_len, "cat:%s", models_base_name(&entity->base));
        return written > 0 && (size_t)written < out_len;
    }
    return models_base_describe(&entity->base, out, out_len);
}
