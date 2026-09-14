//
// Created by tommaso-peduzzi on 10/29/25.
//

#include <iostream>
#include <ostream>
#include <variant>

#include "CAYGDecoder.h"
#include "DecodingGraph.h"
#include "Logger.h"

using namespace std;

int main()
{
    int D = 5;
    vector<pair<string, unordered_map<string, string>>> decoder_constructors = {
        {"clayg", {}},
        {"uf", {}},
    };
    vector<shared_ptr<Decoder>> decoders;
    for (auto& [decoder_name, args] : decoder_constructors)
    {
        shared_ptr<Decoder> decoder;
        if (decoder_name == "clayg")
            decoder = make_shared<CAYGDecoder>(args);
        else if (decoder_name == "sl_clayg")
            decoder = make_shared<SingleLayerCAYGDecoder>(args);
        else if (decoder_name == "uf")
            decoder = make_shared<UnionFindDecoder>(args);
        decoders.push_back(decoder);
    }

    shared_ptr<DecodingGraph> decoding_graph = DecodingGraph::rotated_surface_code(D, D);

    logger.set_dump_dir("data/surface_code_check");
    logger.set_dump_enabled(true);

    variant<string, int> run_id = "check";
    auto next_run_id = [](variant<string, int> current_run_id) {
        if (holds_alternative<string>(current_run_id))
            return false;
        current_run_id = get<int>(current_run_id) + 1;
        if (get<int>(current_run_id) > 100)
            return false;
        return true;
    };
    auto run_id_to_string = [](variant<string, int> run_id) {
        if (holds_alternative<string>(run_id))
            return get<string>(run_id);
        return to_string(get<int>(run_id));
    };

    double error_rate = 0.04;

    vector<DecodingGraphEdge::Id> fixed_error_ids = {
        {DecodingGraphEdge::Type::NORMAL, 1,24},
        {DecodingGraphEdge::Type::NORMAL, 3,1},
        {DecodingGraphEdge::Type::NORMAL, 8,24},
    };

    do {
        logger.set_run_id(run_id_to_string(run_id));
        logger.prepare_dump_dir();

        decoding_graph->reset();
        logger.log_graph(decoding_graph);

        auto error_ids = fixed_error_ids;
        vector<shared_ptr<DecodingGraphEdge>> error_edges;
        if (fixed_error_ids.empty())
        {
            for (const auto& edge : decoding_graph->edges())
            {
                if ((double) rand() / RAND_MAX < error_rate)
                {
                    error_ids.push_back(edge->id());
                }
            }
        }

        for (const auto& decoder : decoders)
        {
            decoding_graph->reset();
            error_edges = {};
            for (auto error_id : error_ids)
                error_edges.push_back(decoding_graph->edge(error_id).value());
            decoding_graph->mark(error_edges);

            auto decoding_result = decoder->decode(decoding_graph);
            vector<DecodingGraphEdge::Id> correction_ids;
            for (const auto& edge : decoding_result.corrections)
                correction_ids.push_back(edge->id());

            logger.log_errors(error_ids);
            logger.log_corrections(correction_ids, decoding_result.correction_steps, decoder->decoder_name());
        }
    } while (next_run_id(run_id));
}
