#ifndef SERVICES_PROCESSOR_H
#define SERVICES_PROCESSOR_H

#include <stdbool.h>
#include <stddef.h>

#include "models/entities.h"

bool services_processor_process(const Entity *entity, char *out, size_t out_len);
size_t services_processor_process_batch(const Entity *entities, size_t count, char outputs[][128]);

#endif
