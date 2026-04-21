#include <iostream>
#include <memory>
#include <vector>

#include "models/entities.hpp"
#include "services/processor.hpp"

int main() {
    std::vector<std::unique_ptr<models::Entity>> animals;
    animals.emplace_back(std::make_unique<models::Cat>("Whiskers"));
    animals.emplace_back(std::make_unique<models::Dog>("Rex"));

    services::Processor processor;
    for (const auto& animal : animals) {
        std::cout << processor.process(*animal) << "\n";
    }

    return 0;
}
