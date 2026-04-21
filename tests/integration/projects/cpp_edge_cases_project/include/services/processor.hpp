#pragma once

#include <algorithm>
#include <string>
#include <vector>

#include "models/base.hpp"

namespace services {

class Processor {
public:
    std::string process(const models::Entity& entity) const;
    std::vector<std::string> process_batch(const std::vector<models::Entity*>& entities) const;
};

// Header-only template helper: counts how many entities describe differently.
template <typename Iter>
std::size_t count_unique_descriptions(Iter begin, Iter end) {
    std::vector<std::string> seen;
    for (Iter it = begin; it != end; ++it) {
        const std::string desc = (*it).describe();
        if (std::find(seen.begin(), seen.end(), desc) == seen.end()) {
            seen.push_back(desc);
        }
    }
    return seen.size();
}

}  // namespace services
