//
// Created by tommasopeduzzi on 1/28/24.
//

#include <cmath>
#include <utility>

#include "CAYGDecoder.h"
#include "Logger.h"
#include "PeelingDecoder.h"

using namespace std;

CAYGDecoder::CAYGDecoder(const std::unordered_map<std::string, std::string>& args)
    : UnionFindDecoder(args)
{
    this->decoder_name_.replace(0, 2, "cayg");

    if (auto it = args.find("cluster_lifetime"); it != args.end()) {
        this->set_cluster_lifetime_factor(stod(it->second));
        this->decoder_name_ += "_lifetime_" + it->second;
    }

    if (auto it = args.find("growth_rounds"); it != args.end()) {
        this->set_growth_rounds(stoi(it->second));
        this->decoder_name_ += "_growth_rounds_" + it->second;
    }
}

DecodingResult CAYGDecoder::decode(shared_ptr<DecodingGraph> graph)
{
    auto marked_nodes_by_round = graph->marked_nodes_by_round();

    const int rounds = graph->t();
    if (!decoding_graph_ || decoding_graph_->d() != graph->d())
    {
        decoding_graph_ = make_shared<DecodingGraph>(*graph);
    }
    decoding_graph_->reset();
    vector<shared_ptr<DecodingGraphEdge>> error_edges;
    // Decoding step at which each correction in `error_edges` arrived (parallel vector).
    vector<int> correction_steps;
    auto append_corrections = [&](const vector<shared_ptr<DecodingGraphEdge>>& corrections, int arrived_at_step)
    {
        error_edges.insert(error_edges.end(), corrections.begin(), corrections.end());
        correction_steps.insert(correction_steps.end(), corrections.size(), arrived_at_step);
    };

    m_clusters = {};
    int step = 0;
    int considered_up_to_round = rounds - 1;
    int last_encountered_non_neutral_cluster = 0;
    double growth_steps = -(rounds-1); // don't count last round as being negative
    double max_growth_steps = growth_steps;
    logger.log_decoding_step(m_clusters, decoder_name_, step++, rounds);
    for (current_round_ = 0; current_round_ < rounds; current_round_++)
    {
        growth_steps = ceil(growth_steps);
        for (const auto& node : marked_nodes_by_round[current_round_])
        {
            add(decoding_graph_, node);
        }
        auto peeling_results = clean(decoding_graph_);
        // Corrections arrive at the step just logged, where the peeled clusters are gone.
        int correction_step = step;
        logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);
        append_corrections(peeling_results.corrections, correction_step);
        // Growth after adding last round belongs to the bulk growth
        double fixed_growth_steps = growth_steps_fixed(growth_steps,
            peeling_results.decoding_steps/growth_rounds_);
        max_growth_steps = max(max_growth_steps, fixed_growth_steps);
        if (current_round_ == rounds-1)
            break;
        for (int growth_round = 0; growth_round < growth_rounds_; growth_round++)
        {
            vector<DecodingGraphEdge::FusionEdge> fusion_edges;
            for (const auto& cluster : m_clusters)
            {
                if (cluster->is_neutral()) continue;
                auto new_fusion_edges = grow(cluster);
                for (const auto& fusion_edge : new_fusion_edges)
                {
                    fusion_edges.push_back(fusion_edge);
                }
            }
            logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);
            merge(fusion_edges);
            logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);
            growth_steps += (1.0/growth_rounds_);
            if (stop_early_ && Cluster::all_clusters_are_neutral(m_clusters))
            {
                break;
            }
        }
        logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);
        peeling_results = clean(decoding_graph_);
        correction_step = step;
        logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);
        append_corrections(peeling_results.corrections, correction_step);
        fixed_growth_steps = growth_steps_fixed(growth_steps,
            peeling_results.decoding_steps/growth_rounds_);
        max_growth_steps = max(max_growth_steps, fixed_growth_steps);

        if (stop_early_ && Cluster::all_clusters_are_neutral(m_clusters))
        {
            int buffer_region = (decoding_graph_->d()+1)/2;
            if (current_round_-last_encountered_non_neutral_cluster >= buffer_region)
            {
                considered_up_to_round = current_round_;
                break;
            }
        }
        else
        {
            last_encountered_non_neutral_cluster = current_round_;
        }
    }

    logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);

    while (!Cluster::all_clusters_are_neutral(m_clusters))
    {
        growth_steps += 1;
        vector<DecodingGraphEdge::FusionEdge> fusion_edges;
        for (const auto& cluster : m_clusters)
        {
            if (cluster->is_neutral()) continue;
            auto new_fusion_edges = grow(cluster);
            for (const auto& fusion_edge : new_fusion_edges)
            {
                fusion_edges.push_back(fusion_edge);
            }
        }
        logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);
        merge(fusion_edges);
        logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);
    }

    auto peeling_result = PeelingDecoder::decode(m_clusters, decoding_graph_);
    max_growth_steps = max(max_growth_steps,growth_steps + peeling_result.decoding_steps);
    // Final corrections arrive at the last step, where all clusters have been peeled away.
    append_corrections(peeling_result.corrections, step);
    logger.log_decoding_step({}, decoder_name_, step++, current_round_);

    DecodingResult result;
    result.corrections = error_edges;
    result.considered_up_to_round = considered_up_to_round;
    result.decoding_steps = max_growth_steps;
    result.correction_steps = correction_steps;
    return result;
}

void CAYGDecoder::merge(const vector<DecodingGraphEdge::FusionEdge>& fusion_edges)
{
    for (const auto& fusion_edge : fusion_edges)
    {
        auto tree_node = fusion_edge.tree_node;;
        // assert that tree node has cluster
        assert(tree_node->cluster().has_value());
        auto leaf_node = fusion_edge.leaf_node;

        auto cluster = tree_node->cluster().value().lock();
        auto other_cluster_optional = leaf_node->cluster();

        if (!other_cluster_optional.has_value())
        {
            // node is not part of another cluster
            cluster->add_node(leaf_node);
            if (leaf_node->marked())
                cluster->add_marked_node(leaf_node);
            if (leaf_node->id().type == DecodingGraphNode::VIRTUAL)
                cluster->add_virtual_node(leaf_node);

            cluster->add_bulk_edge(fusion_edge.edge);
            for (const auto& edge_weak_ptr : leaf_node->edges())
            {
                auto edge = edge_weak_ptr.lock();
                if (edge != fusion_edge.edge)
                {
                    cluster->add_boundary_edge(Cluster::BoundaryEdge{
                        leaf_node,
                        edge->other_node(leaf_node).lock(),
                        edge
                    });
                }
            }
            leaf_node->set_cluster(cluster);
            if (cluster->is_neutral())
                cluster->set_has_been_neutral_since(current_round_);
            continue;
        }

        auto other_cluster = other_cluster_optional.value().lock();

        if (other_cluster == cluster)
        {
            continue;
        }

        for (const auto& node : other_cluster->nodes())
        {
            cluster->add_node(node);
            if (node->id().type == DecodingGraphNode::VIRTUAL)
            {
                cluster->add_virtual_node(node);
            }
            if (node->marked())
            {
                cluster->add_marked_node(node);
            }
            node->set_cluster(cluster);
        }
        for (const auto& edge : other_cluster->edges())
        {
            cluster->add_bulk_edge(edge);
        }
        for (const auto& boundary : other_cluster->boundary())
        {
            cluster->add_boundary_edge(boundary);
        }

        if (cluster->is_neutral())
            cluster->set_has_been_neutral_since(current_round_);

        auto cluster_to_be_removed = find(m_clusters.begin(), m_clusters.end(), other_cluster);
        if (cluster_to_be_removed != m_clusters.end())
            m_clusters.erase(cluster_to_be_removed);
    }
}

void CAYGDecoder::add(const shared_ptr<DecodingGraph>& graph, shared_ptr<DecodingGraphNode> node)
{
    // Find corresponding node in the flattened decoding graph
    auto id = node->id();
    node = graph->node(id).value();
    node->set_marked(!node->marked());
    if (node->cluster().has_value())
    {
        auto cluster = node->cluster().value().lock();
        if (node->marked())
        {
            cluster->add_marked_node(node);
        }
        else
        {
            cluster->remove_marked_node(node);
        }
    }
    else
    {
        auto new_cluster = make_shared<Cluster>(node);
        node->set_cluster(new_cluster);
        if (node->marked())
        {
            new_cluster->add_marked_node(node);
        }
        if (node->id().type == DecodingGraphNode::VIRTUAL)
        {
            new_cluster->add_virtual_node(node);
        }
        m_clusters.push_back(new_cluster);
    }
}

DecodingResult CAYGDecoder::clean(const shared_ptr<DecodingGraph>& decoding_graph)
{
    vector<shared_ptr<DecodingGraphEdge>> error_edges;
    double peeling_steps = 0;
    vector<shared_ptr<Cluster>> new_clusters;
    for (auto& cluster : m_clusters)
    {
        // Keep non-neutral-clusters around
        if (!cluster->is_neutral())
        {
            new_clusters.push_back(move(cluster));
            continue;
        }

        // Keep newly neutral clusters around.
        int cluster_lifetime = 0;
        if (cluster_lifetime_factor_ < 1)
        {
            cluster_lifetime = decoding_graph->d() * cluster_lifetime_factor_;
        }
        else {
            cluster_lifetime = static_cast<int>(cluster_lifetime_factor_);
        }

        if (current_round_ - cluster->has_been_neutral_since() < cluster_lifetime)
        {
            new_clusters.push_back(move(cluster));
            continue;
        }

        // Peel older, neutral clusters.
        auto cluster_peel_results = PeelingDecoder::peel(cluster, decoding_graph);
        auto new_error_edges = cluster_peel_results.corrections;
        peeling_steps = max(peeling_steps, cluster_peel_results.decoding_steps);
        error_edges.insert(error_edges.end(), new_error_edges.begin(), new_error_edges.end());

        for (const auto& node : cluster->nodes())
        {
            node->set_cluster(nullopt);
        }
        for (const auto& edge : cluster->edges())
        {
            edge->reset_growth();
        }

        for (const auto& boundary_edge : cluster->boundary())
        {
            boundary_edge.edge->add_growth(-boundary_edge.growth_from_tree);
        }
    }
    m_clusters = move(new_clusters);
    return {error_edges, 0,peeling_steps};
}

SingleLayerCAYGDecoder::SingleLayerCAYGDecoder(const std::unordered_map<std::string, std::string>& args)
        : CAYGDecoder(args)
{
    decoder_name_ = "sl_" + decoder_name_;
}

DecodingResult SingleLayerCAYGDecoder::decode(shared_ptr<DecodingGraph> graph)
{
    auto marked_nodes_by_round = graph->marked_nodes_by_round();

    const int rounds = graph->t();
    if (!decoding_graph_ || decoding_graph_->d() != graph->d())
    {
        decoding_graph_ = DecodingGraph::single_layer_copy(graph);
    }
    else
    {
        decoding_graph_->reset(); // Reset the graph to its initial state
    }

    vector<shared_ptr<DecodingGraphEdge>> error_edges;
    // Decoding step at which each correction in `error_edges` arrived (parallel vector).
    vector<int> correction_steps;
    auto append_corrections = [&](const vector<shared_ptr<DecodingGraphEdge>>& corrections, int arrived_at_step)
    {
        error_edges.insert(error_edges.end(), corrections.begin(), corrections.end());
        correction_steps.insert(correction_steps.end(), corrections.size(), arrived_at_step);
    };

    m_clusters = {};
    int step = 0;
    int considered_up_to_round = rounds - 1;
    int last_encountered_non_neutral_cluster = 0;
    double growth_steps = -(rounds-1);
    double max_growth_steps = growth_steps;
    for (current_round_ = 0; current_round_ < rounds; current_round_++)
    {
        growth_steps = ceil(growth_steps);
        for (const auto& node : marked_nodes_by_round[current_round_])
        {
            add(decoding_graph_, node);
        }
        auto result = clean(decoding_graph_);
        max_growth_steps = max(max_growth_steps, growth_steps + result.decoding_steps);
        // Corrections arrive at the step just logged, where the peeled clusters are gone.
        int correction_step = step;
        logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);
        append_corrections(result.corrections, correction_step);
        // Growth after adding last round belongs to the bulk growth
        if (current_round_ == rounds-1)
            break;
        for (int growth_round = 0; growth_round < growth_rounds_; growth_round++)
        {
            vector<DecodingGraphEdge::FusionEdge> fusion_edges;
            for (const auto& cluster : m_clusters)
            {
                if (cluster->is_neutral()) continue;
                auto new_fusion_edges = grow(cluster);
                for (const auto& fusion_edge : new_fusion_edges)
                {
                    fusion_edges.push_back(fusion_edge);
                }
            }
            logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);
            merge(fusion_edges);
            logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);
            growth_steps += 1.0/growth_rounds_;
            if (stop_early_ && Cluster::all_clusters_are_neutral(m_clusters))
            {
                break;
            }
        }
        auto peeling_results = clean(decoding_graph_);
        double fixed_growth_steps = growth_steps_fixed(growth_steps,
            peeling_results.decoding_steps/growth_rounds_);
        max_growth_steps = max(max_growth_steps,  fixed_growth_steps);
        correction_step = step;
        logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);
        append_corrections(peeling_results.corrections, correction_step);
        if (stop_early_ && Cluster::all_clusters_are_neutral(m_clusters))
        {
            if (current_round_-last_encountered_non_neutral_cluster >= (decoding_graph_->d()-1)/2)
            {
                considered_up_to_round = current_round_;
                break;
            }
        }
        else
        {
            last_encountered_non_neutral_cluster = current_round_;
        }
    }

    while (!Cluster::all_clusters_are_neutral(m_clusters))
    {
        growth_steps += 1;
        vector<DecodingGraphEdge::FusionEdge> fusion_edges;
        for (const auto& cluster : m_clusters)
        {
            if (cluster->is_neutral()) continue;
            auto new_fusion_edges = grow(cluster);
            for (const auto& fusion_edge : new_fusion_edges)
            {
                fusion_edges.push_back(fusion_edge);
            }
        }
        logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);
        merge(fusion_edges);
        logger.log_decoding_step(m_clusters, decoder_name_, step++, current_round_);
    }

    auto [peeling_error_edges, _, steps, peeling_correction_steps] = PeelingDecoder::decode(m_clusters, decoding_graph_);
    max_growth_steps = max(max_growth_steps, growth_steps + steps);
    // Final corrections arrive at the last step, where all clusters have been peeled away.
    append_corrections(peeling_error_edges, step);
    logger.log_decoding_step({}, decoder_name_, step++, current_round_);
    DecodingResult result;
    result.corrections = error_edges;
    result.considered_up_to_round = considered_up_to_round;
    result.decoding_steps = max_growth_steps;
    result.correction_steps = correction_steps;
    return result;
}

void SingleLayerCAYGDecoder::add(const shared_ptr<DecodingGraph>& graph, shared_ptr<DecodingGraphNode> node)
{
    // Find corresponding node in the flattened decoding graph
    auto id = node->id();
    id.round = 0;
    node = graph->node(id).value();
    node->set_marked(!node->marked());
    if (node->cluster().has_value())
    {
        auto cluster = node->cluster().value().lock();
        if (node->marked())
        {
            cluster->add_marked_node(node);
        }
        else
        {
            cluster->remove_marked_node(node);
        }
        if (cluster->is_neutral())
        {
            cluster->set_has_been_neutral_since(current_round_);
        }
    }
    else
    {
        auto new_cluster = make_shared<Cluster>(node);
        node->set_cluster(new_cluster);
        if (node->marked())
        {
            new_cluster->add_marked_node(node);
        }
        if (node->id().type == DecodingGraphNode::VIRTUAL)
        {
            new_cluster->add_virtual_node(node);
        }
        m_clusters.push_back(new_cluster);
    }
}
