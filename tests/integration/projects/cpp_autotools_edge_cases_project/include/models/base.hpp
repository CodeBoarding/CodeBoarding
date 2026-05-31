#pragma once

#include <string>

namespace models {

class Entity {
public:
    explicit Entity(std::string name);
    virtual ~Entity() = default;
    virtual std::string describe() const;
    std::string name() const;

private:
    std::string name_;
};

class Speaker : public Entity {
public:
    explicit Speaker(std::string name);
    virtual std::string speak() const = 0;
    std::string shout() const;
};

}  // namespace models
