#pragma once

#include <string>

namespace greet {

class Greeter {
public:
    explicit Greeter(std::string name);
    std::string hello() const;

private:
    std::string name_;
};

}  // namespace greet
