#include <iostream>

#include "greeter.hpp"

int main() {
    demo::Greeter greeter("world");
    std::cout << greeter.greet() << "\n";
    return 0;
}
