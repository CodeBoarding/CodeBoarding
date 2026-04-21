#include <iostream>

#include "models/entities.hpp"
#include "services/processor.hpp"

int main() {
    models::Cat cat("Whiskers");
    models::Dog dog("Rex");

    services::Processor processor;
    std::cout << processor.process(cat) << "\n";
    std::cout << processor.process(dog) << "\n";

    return 0;
}
