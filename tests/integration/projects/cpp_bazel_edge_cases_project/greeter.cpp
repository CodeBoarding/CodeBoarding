#include "greeter.hpp"

#include <utility>

namespace greet {

Greeter::Greeter(std::string name) : name_(std::move(name)) {}

std::string Greeter::hello() const {
    return "hello, " + name_;
}

}  // namespace greet
