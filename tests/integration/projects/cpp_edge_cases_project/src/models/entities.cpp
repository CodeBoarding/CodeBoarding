#include "models/entities.hpp"

#include <utility>

namespace models {

Cat::Cat(std::string name) : Speaker(std::move(name)) {}

std::string Cat::speak() const {
    return "meow";
}

std::string Cat::describe() const {
    return "cat:" + name();
}

Dog::Dog(std::string name) : Speaker(std::move(name)) {}

std::string Dog::speak() const {
    return "woof";
}

}  // namespace models
