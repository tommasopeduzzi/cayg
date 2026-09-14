//
// Created by tommasopeduzzi on 1/8/24.
//

#include "DecodingGraph.h"
#include "Logger.h"

using namespace std;

shared_ptr<DecodingGraph> DecodingGraph::surface_code(int D, int T) {
    int ancilla_height = D - 1;
    int ancilla_width = D - 1;

    auto graph = make_shared<DecodingGraph>();

    graph->m_ancilla_count_per_layer = ancilla_height * ancilla_width;
    graph->D = D;
    graph->T = T;
    graph->code_name_ = "surface_code";

    const shared_ptr<DecodingGraphNode> top_boundary_node = make_shared<DecodingGraphNode>(
        DecodingGraphNode::Id{DecodingGraphNode::Type::VIRTUAL, 0, 0});
    const shared_ptr<DecodingGraphNode> bottom_boundary_node = make_shared<DecodingGraphNode>(
        DecodingGraphNode::Id{DecodingGraphNode::Type::VIRTUAL, 0, 1});
    graph->addNode(top_boundary_node);
    graph->addNode(bottom_boundary_node);

    for (int t = 0; t < T; t++) {
        DecodingGraphEdge::Id current_edge_id = {DecodingGraphEdge::NORMAL, t, 0};
        for (int y = 0; y < ancilla_height; y++) {
            for (int x = 0; x < ancilla_width; x++) {
                auto id = DecodingGraphNode::Id{DecodingGraphNode::Type::ANCILLA, t, x + y * ancilla_width};
                auto node = make_shared<DecodingGraphNode>(id);
                graph->addNode(node);

                if (t > 0) {
                    auto other_node = DecodingGraphNode::Id{DecodingGraphNode::Type::ANCILLA, t - 1,
                                                            x + y * ancilla_width};
                    auto nodes = make_pair(node, graph->node(other_node).value());
                    auto edge = make_shared<DecodingGraphEdge>(DecodingGraphEdge::Id{DecodingGraphEdge::MEASUREMENT, t - 1, x + y * ancilla_width}, nodes);
                    graph->addEdge(edge);
                }

                if (y == 0) {
                    auto nodes = make_pair(node, top_boundary_node);
                    graph->addEdge(make_shared<DecodingGraphEdge>(
                            current_edge_id, nodes));
                    current_edge_id++;
                } else {
                    auto other_node = DecodingGraphNode::Id{DecodingGraphNode::Type::ANCILLA, 0,
                                                            x + (y - 1) * ancilla_width};
                    auto nodes = make_pair(node, graph->node(other_node).value());
                    graph->addEdge(make_shared<DecodingGraphEdge>(
                            current_edge_id, nodes));
                    current_edge_id++;
                }

                if (x > 0) {
                    auto other_node = DecodingGraphNode::Id{DecodingGraphNode::Type::ANCILLA, 0,
                                                            x - 1 + y * ancilla_width};
                    auto nodes = make_pair(node, graph->node(other_node).value());
                    auto edge = make_shared<DecodingGraphEdge>(current_edge_id, nodes);
                    current_edge_id++;
                    graph->addEdge(edge);
                    if (y == 0) {
                        graph->addLogicalEdge(edge);
                    }
                }

                if (y == ancilla_height - 1) {
                    auto nodes = make_pair(node, bottom_boundary_node);
                    graph->addEdge(make_shared<DecodingGraphEdge>(
                            current_edge_id, nodes));
                    current_edge_id++;
                }
            }
        }
    }

    return graph;
}

shared_ptr<DecodingGraph> DecodingGraph::rotated_surface_code(int D, int T) {
    int ancilla_height = D - 1;
    int ancilla_width = (D+1)/2;

    auto graph = make_shared<DecodingGraph>();

    graph->m_ancilla_count_per_layer = ancilla_height * ancilla_width;
    graph->D = D;
    graph->T = T;
    graph->code_name_ = "rotated_surface_code";

    const shared_ptr<DecodingGraphNode> top_boundary_node = make_shared<DecodingGraphNode>(
        DecodingGraphNode::Id{DecodingGraphNode::Type::VIRTUAL, 0, 0});
    const shared_ptr<DecodingGraphNode> bottom_boundary_node = make_shared<DecodingGraphNode>(
        DecodingGraphNode::Id{DecodingGraphNode::Type::VIRTUAL, 0, 1});
    graph->addNode(top_boundary_node);
    graph->addNode(bottom_boundary_node);

    for (int t = 0; t < T; t++) {
        pair<shared_ptr<DecodingGraphNode>, shared_ptr<DecodingGraphNode>> nodes{};
        DecodingGraphEdge::Id current_edge_id{DecodingGraphEdge::NORMAL, t, 0};
        for (int y = 0; y < ancilla_height; y++) {
            for (int x = 0; x < ancilla_width; x++) {
                auto id = DecodingGraphNode::Id{DecodingGraphNode::Type::ANCILLA, t, x + y * ancilla_width};
                shared_ptr<DecodingGraphNode> node = make_shared<DecodingGraphNode>(id);
                graph->addNode(node);

                // Connect to previous set of ancillas
                if (t > 0) {
                    auto edge_id = DecodingGraphEdge::Id{DecodingGraphEdge::MEASUREMENT, t - 1, x + y * ancilla_width};
                    auto prev_id = DecodingGraphNode::Id{DecodingGraphNode::ANCILLA, t - 1, x + y * ancilla_width};
                    nodes = make_pair(node, graph->node(prev_id).value());
                    graph->addEdge(make_shared<DecodingGraphEdge>(edge_id, nodes));
                }

                // Connect to top boundary
                if (y == 0) {
                    nodes = make_pair(node, top_boundary_node);
                    graph->addEdge(make_shared<DecodingGraphEdge>(current_edge_id, nodes));
                    current_edge_id++;
                    if (x + 1 < ancilla_width) {
                        nodes = make_pair(node, top_boundary_node);
                        graph->addEdge(make_shared<DecodingGraphEdge>(current_edge_id, nodes));
                        current_edge_id++;
                    }
                    continue;
                }
                if (y % 2 == 0) {
                    auto other_node_id = DecodingGraphNode::Id{DecodingGraphNode::ANCILLA, t,
                                                               x + (y - 1) * ancilla_width};
                    nodes = make_pair(node, graph->node(other_node_id).value());
                    graph->addEdge(make_shared<DecodingGraphEdge>(current_edge_id, nodes));
                    current_edge_id++;
                    if (x + 1 < ancilla_width) {
                        other_node_id = DecodingGraphNode::Id{DecodingGraphNode::ANCILLA, t,
                                                              x + (y - 1) * ancilla_width + 1};
                        nodes = make_pair(node, graph->node(other_node_id).value());
                        graph->addEdge(make_shared<DecodingGraphEdge>(current_edge_id, nodes));
                        current_edge_id++;
                    }
                } else {
                    if (x - 1 >= 0) {
                        auto other_node_id = DecodingGraphNode::Id{DecodingGraphNode::ANCILLA, t,
                                                                   x + (y - 1) * ancilla_width - 1};
                        nodes = make_pair(node, graph->node(other_node_id).value());
                        auto edge = make_shared<DecodingGraphEdge>(current_edge_id, nodes);
                        graph->addEdge(edge);
                        current_edge_id++;
                    }
                    auto other_node_id = DecodingGraphNode::Id{DecodingGraphNode::ANCILLA, t,
                                                               x + (y - 1) * ancilla_width};
                    nodes = make_pair(node, graph->node(other_node_id).value());
                    auto edge = make_shared<DecodingGraphEdge>(current_edge_id, nodes);
                    graph->addEdge(edge);
                    current_edge_id++;
                }

            }
        }
        for (int x = 0; x < ancilla_width; x++) {
            auto id = DecodingGraphNode::Id{DecodingGraphNode::ANCILLA, t, x + ancilla_width * (ancilla_height - 1)};
            auto node = graph->node(id).value();
            if (x - 1 >= 0) {
                graph->addEdge(make_shared<DecodingGraphEdge>(current_edge_id,
                                                              make_pair(node, bottom_boundary_node)));
                current_edge_id++;
            }
            graph->addEdge(make_shared<DecodingGraphEdge>(current_edge_id,
                                                          make_pair(node, bottom_boundary_node)));
            current_edge_id++;
        }
    }

    for (int id = 0; id < D; id++)
    {
        auto edge = graph->edge(DecodingGraphEdge::Id{DecodingGraphEdge::NORMAL, 0, id}).value();
        graph->addLogicalEdge(edge);
    }

    return graph;
}

std::shared_ptr<DecodingGraph> DecodingGraph::repetition_code(int D, int T)
{
    auto graph = make_shared<DecodingGraph>();

    graph->m_ancilla_count_per_layer = D;
    graph->D = D;
    graph->T = T;
    graph->code_name_ = "repetition_code";

    auto left_boundary_node = make_shared<DecodingGraphNode>(DecodingGraphNode::Id{DecodingGraphNode::VIRTUAL, 0,0});
    auto right_boundary_node = make_shared<DecodingGraphNode>(DecodingGraphNode::Id{DecodingGraphNode::VIRTUAL, 0,1});
    graph->addNode(left_boundary_node);
    graph->addNode(right_boundary_node);

    for (int round = 0; round < T; round++)
    {
        for (int x = 0; x < D; x++)
        {
            auto node = make_shared<DecodingGraphNode>(DecodingGraphNode::Id {DecodingGraphNode::ANCILLA, round, x});
            graph->addNode(node);
            shared_ptr<DecodingGraphNode> other_node;
            if (x == 0)
            {
                other_node = left_boundary_node;
            }
            else
            {
                other_node = graph->node({DecodingGraphNode::Type::ANCILLA, round, x-1}).value();
            }
            graph->addEdge(make_shared<DecodingGraphEdge>(
                DecodingGraphEdge::Id {DecodingGraphEdge::Type::NORMAL, round, x},
                make_pair(
                    other_node,
                    node)));

            // connect to previous round
            if (round == 0)
                continue;

            auto previous_round = graph->node({DecodingGraphNode::ANCILLA, round-1, x}).value();
            graph->addEdge(make_shared<DecodingGraphEdge>(
                DecodingGraphEdge::Id{DecodingGraphEdge::Type::MEASUREMENT, round, x},
                make_pair(node,previous_round)));
        }

        auto rightmost_node = graph->node({DecodingGraphNode::Type::ANCILLA, round, D-1}).value();
        graph->addEdge(make_shared<DecodingGraphEdge>(
                DecodingGraphEdge::Id {DecodingGraphEdge::Type::NORMAL, round, D+1},
                make_pair(rightmost_node, right_boundary_node)));
    }

    return graph;
}

std::shared_ptr<DecodingGraph> DecodingGraph::single_layer_copy(std::shared_ptr<DecodingGraph> graph)
{
    if (graph->code_name() == "rotated_surface_code")
    {
        return DecodingGraph::rotated_surface_code(graph->d(), 1);
    } if (graph->code_name() == "surface_code")
    {
        return DecodingGraph::surface_code(graph->d(), 1);
    } if (graph->code_name() == "repetition_code")
    {
        return DecodingGraph::repetition_code(graph->d(), 1);
    }
    throw runtime_error("SingleLayerCAYGDecoder: Unsupported code type " + graph->code_name());
}

optional<shared_ptr<DecodingGraphNode>> DecodingGraph::node(const DecodingGraphNode::Id id) {
    if (id.type == DecodingGraphNode::Type::VIRTUAL) {
        return m_virtual_nodes[id.id];
    }
    if (id.type == DecodingGraphNode::Type::ANCILLA) {
        if (m_ancilla_nodes.size() <= id.round)
            return {};
        return m_ancilla_nodes[id.round][id.id];
    }
    return {};
}

optional<shared_ptr<DecodingGraphEdge>> DecodingGraph::edge(const DecodingGraphEdge::Id id) {
    if (id.type == DecodingGraphEdge::MEASUREMENT) {
        if (m_measurement_edges.size() <= id.round)
            return {};
        return m_measurement_edges[id.round][id.id];
    }
    if (id.type == DecodingGraphEdge::NORMAL) {
        if (m_normal_edges.size() <= id.round)
            return {};
        return m_normal_edges[id.round][id.id];
    }
    return {};
}

void DecodingGraph::addEdge(const shared_ptr<DecodingGraphEdge>& edge) {
    m_edges.push_back(edge);
    auto [type, round, id] = edge->id();
    if (type == DecodingGraphEdge::NORMAL) {
        if (m_normal_edges.size() <= round) {
            m_normal_edges.resize(round + 1);
        }
        m_normal_edges[round][id] = edge;
    } else if (type == DecodingGraphEdge::MEASUREMENT) {
        if (m_measurement_edges.size() <= round) {
            m_measurement_edges.resize(round + 1);
        }
        m_measurement_edges[round][id] = edge;
    }
    edge->nodes().first.lock()->add_edge(edge);
    edge->nodes().second.lock()->add_edge(edge);
}

void DecodingGraph::addLogicalEdge(const shared_ptr<DecodingGraphEdge>& edge) {
    m_logical_edges.push_back(edge->id());
}

void DecodingGraph::addNode(const shared_ptr<DecodingGraphNode>& node) {
    m_nodes.push_back(node);
    auto id = node->id();
    if (id.type == DecodingGraphNode::Type::ANCILLA) {
        if (m_ancilla_nodes.size() <= id.round) {
            m_ancilla_nodes.resize(id.round + 1);
        }
        m_ancilla_nodes[id.round][id.id] = node;
    } else {
        m_virtual_nodes[id.id] = node;
    }
}

set<int> DecodingGraph::logical_edge_ids() {
    set<int> ids;
    for (auto edge: m_logical_edges) {
        ids.insert(edge.id);
    }
    return ids;
}

void DecodingGraph::reset() {
    for (const auto& node: m_nodes) {
        node->set_cluster(nullopt);
        node->set_marked(false);
    }
    for (const auto& edge: m_edges) {
        edge->reset_growth();
    }
}

void DecodingGraph::mark(const std::vector<std::shared_ptr<DecodingGraphEdge>>& error_edges)
{
    for (const auto& edge : error_edges)
    {
        auto nodes = {edge->nodes().first, edge->nodes().second};
        for (const auto& node_weak_ptr : nodes)
        {
            auto node = node_weak_ptr.lock();
            if (node->id().type == DecodingGraphNode::VIRTUAL)
            {
                continue;
            }
            node->set_marked(!node->marked());
        }
    }
}

std::vector<std::vector<std::shared_ptr<DecodingGraphNode>>> DecodingGraph::marked_nodes_by_round()
{
    vector<vector<shared_ptr<DecodingGraphNode>>> marked_nodes;
    for (int round = 0; round < T; round++)
    {
        marked_nodes.push_back({});
    }
    for (const auto& node : m_nodes)
    {
        if (node->marked())
        {
            marked_nodes[node->id().round].push_back(node);
        }
    }
    return marked_nodes;
}

std::vector<DecodingGraphEdge::Id> DecodingGraph::sample_errors(
    double p,
    const std::map<DecodingGraphEdge::Type, double>& noise_model,
    int sample_T,
    std::uniform_real_distribution<double>& dis,
    std::mt19937& gen) const
{
    std::vector<DecodingGraphEdge::Id> error_ids;
    int use_T = (sample_T < 0) ? T : sample_T;

    for (int t = 0; t < use_T; t++)
    {
        // sample normal edges at round t
        if (t < static_cast<int>(m_normal_edges.size())) {
            for (const auto& [id, edge] : m_normal_edges[t]) {
                double factor = 1.0;
                auto it = noise_model.find(DecodingGraphEdge::NORMAL);
                if (it != noise_model.end()) factor = it->second;
                double p_effective = p * factor;
                if (p_effective > 0 && dis(gen) <= p_effective) {
                    error_ids.push_back(edge->id());
                }
            }
        }

        // sample measurement edges originating at round t (measurement edges are associated with round t)
        if (t < static_cast<int>(m_measurement_edges.size())) {
            for (const auto& [id, edge] : m_measurement_edges[t]) {
                double factor = 1.0;
                auto it = noise_model.find(DecodingGraphEdge::MEASUREMENT);
                if (it != noise_model.end()) factor = it->second;
                double p_effective = p * factor;
                if (p_effective > 0 && dis(gen) <= p_effective) {
                    error_ids.push_back(edge->id());
                }
            }
        }
    }
    return error_ids;
}
