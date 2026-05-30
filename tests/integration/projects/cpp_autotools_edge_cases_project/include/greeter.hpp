#pragma once

#include <string>

namespace demo {

class Greeter {
public:
    explicit Greeter(std::string name);
    std::string greet() const;

private:
    std::string name_;
};

}  // namespace demo
