#include "greeter.hpp"

#include <utility>

namespace demo {

Greeter::Greeter(std::string name) : name_(std::move(name)) {}

std::string Greeter::greet() const {
    return "hello, " + name_;
}

}  // namespace demo
