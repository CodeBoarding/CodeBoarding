#pragma once

#include "models/base.hpp"

namespace models {

class Cat : public Speaker {
public:
    explicit Cat(std::string name);
    std::string speak() const override;
    std::string describe() const override;
};

class Dog : public Speaker {
public:
    explicit Dog(std::string name);
    std::string speak() const override;
};

}  // namespace models
