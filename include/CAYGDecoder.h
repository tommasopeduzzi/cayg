//
// Created by tommasopeduzzi on 1/28/24.
//

#ifndef CLAYG_CLAYGDECODER_H
#define CLAYG_CLAYGDECODER_H

#include <utility>

#include "UnionFindDecoder.h"
#include "Decoder.h"

// CAYGDecoder inherits from UnionFind
// It owns the clusters and the graph, but not its constituents
class CAYGDecoder : public UnionFindDecoder {
protected:
    int growth_rounds_ = 1;
    int current_round_ = 0;
    double cluster_lifetime_factor_ = 0;
    std::shared_ptr<DecodingGraph> decoding_graph_;

    [[nodiscard]] double growth_steps_fixed(const double current_growth_steps, const double peeling_growth_steps) const {
        double growth_steps = current_growth_steps + peeling_growth_steps;
        if (growth_steps > 0)
            // Growth rounds < 0 are counted as 1/growth_rounds each (not relevant for statistics, just for reporting)
                growth_steps *= growth_rounds_;
        return growth_steps;
    }

public:
    explicit CAYGDecoder(const std::unordered_map<std::string, std::string>& args = {});

    DecodingResult decode(std::shared_ptr<DecodingGraph> graph) override;

    void merge(const std::vector<DecodingGraphEdge::FusionEdge>& fusion_edges) override;

    DecodingResult clean(const std::shared_ptr<DecodingGraph>& decoding_graph);

    virtual void add(const std::shared_ptr<DecodingGraph>& graph, std::shared_ptr<DecodingGraphNode> node);

    void set_growth_rounds(const int growth_rounds) { growth_rounds_ = growth_rounds; }

    void set_cluster_lifetime_factor(const double life_time) { cluster_lifetime_factor_ = life_time; }
};

class SingleLayerCAYGDecoder : public CAYGDecoder
{
public:
    explicit SingleLayerCAYGDecoder(const std::unordered_map<std::string, std::string>& args = {});

    DecodingResult decode(std::shared_ptr<DecodingGraph> graph) override;

    void add(const std::shared_ptr<DecodingGraph>& graph, std::shared_ptr<DecodingGraphNode> node) override;
};

#endif //CLAYG_CLAYGDECODER_H
