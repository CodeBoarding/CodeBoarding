#ifndef MODELS_ENTITIES_H
#define MODELS_ENTITIES_H

#include <stdbool.h>
#include <stddef.h>

#include "models/base.h"

typedef enum EntityKind {
    ENTITY_KIND_CAT = 0,
    ENTITY_KIND_DOG = 1,
} EntityKind;

typedef struct Entity {
    BaseModel base;
    EntityKind kind;
} Entity;

void models_entities_init_cat(Entity *entity, const char *name);
void models_entities_init_dog(Entity *entity, const char *name);
bool models_entities_speak(const Entity *entity, char *out, size_t out_len);
bool models_entities_describe(const Entity *entity, char *out, size_t out_len);

#endif
