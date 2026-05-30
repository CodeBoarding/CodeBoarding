#include "models/base.hpp"

#include <utility>

namespace models {

Entity::Entity(std::string name) : name_(std::move(name)) {}

std::string Entity::describe() const {
    return "entity:" + name_;
}

std::string Entity::name() const {
    return name_;
}

Speaker::Speaker(std::string name) : Entity(std::move(name)) {}

std::string Speaker::shout() const {
    return speak() + "!";
}

}  // namespace models
