#include <stdio.h>

#include "models/entities.h"
#include "services/processor.h"

int main(void) {
    Entity cat;
    Entity dog;
    models_entities_init_cat(&cat, "Whiskers");
    models_entities_init_dog(&dog, "Rex");

    char buffer[128];
    if (services_processor_process(&cat, buffer, sizeof buffer)) {
        printf("%s\n", buffer);
    }
    if (services_processor_process(&dog, buffer, sizeof buffer)) {
        printf("%s\n", buffer);
    }

    Entity batch[] = {cat, dog};
    char outputs[2][128];
    size_t produced = services_processor_process_batch(batch, 2, outputs);
    for (size_t i = 0; i < produced; ++i) {
        printf("%s\n", outputs[i]);
    }

    return 0;
}
