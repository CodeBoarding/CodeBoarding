#ifndef MODELS_BASE_H
#define MODELS_BASE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MODELS_BASE_NAME_MAX 32

typedef struct BaseModel {
    char name[MODELS_BASE_NAME_MAX];
    int32_t id;
} BaseModel;

void models_base_init(BaseModel *model, const char *name, int32_t id);
const char *models_base_name(const BaseModel *model);
bool models_base_describe(const BaseModel *model, char *out, size_t out_len);

#endif
