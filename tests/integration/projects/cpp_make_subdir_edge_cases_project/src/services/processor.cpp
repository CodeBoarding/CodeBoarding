#include "services/processor.hpp"

namespace services {

std::string Processor::process(const models::Entity& entity) const {
    return "processed:" + entity.describe();
}

std::vector<std::string> Processor::process_batch(const std::vector<models::Entity*>& entities) const {
    std::vector<std::string> results;
    results.reserve(entities.size());
    for (const models::Entity* entity : entities) {
        results.push_back(process(*entity));
    }
    return results;
}

}  // namespace services
