#include "models/base.h"

#include <stdio.h>
#include <string.h>

void models_base_init(BaseModel *model, const char *name, int32_t id) {
    *model = (BaseModel){.id = id};
    strncpy(model->name, name, MODELS_BASE_NAME_MAX - 1);
    model->name[MODELS_BASE_NAME_MAX - 1] = '\0';
}

const char *models_base_name(const BaseModel *model) {
    return model->name;
}

bool models_base_describe(const BaseModel *model, char *out, size_t out_len) {
    int written = snprintf(out, out_len, "entity:%s", model->name);
    return written > 0 && (size_t)written < out_len;
}
