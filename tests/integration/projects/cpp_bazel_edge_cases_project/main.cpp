#include <iostream>

#include "greeter.hpp"

int main() {
    greet::Greeter greeter("world");
    std::cout << greeter.hello() << "\n";
    return 0;
}
