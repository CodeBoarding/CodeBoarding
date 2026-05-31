#include <iostream>
#include <vector>

#include "models/entities.hpp"
#include "services/processor.hpp"

int main() {
    models::Cat cat("Whiskers");
    models::Dog dog("Rex");

    services::Processor processor;
    std::cout << processor.process(cat) << "\n";
    std::cout << processor.process(dog) << "\n";

    std::vector<models::Entity*> entities = {&cat, &dog};
    for (const auto& result : processor.process_batch(entities)) {
        std::cout << result << "\n";
    }
    std::cout << services::count_unique_descriptions(entities.begin(), entities.end()) << "\n";

    return 0;
}
