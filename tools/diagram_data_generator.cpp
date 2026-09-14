//
// Created by tommaso-peduzzi on 10/29/25.
//

#include <iostream>
#include <ostream>
#include <variant>
#include <fstream>
#include <regex>
#include <sstream>

#include "CAYGDecoder.h"
#include "DecodingGraph.h"
#include "Logger.h"

using namespace std;

int main()
{
    int D = 7;
    vector<pair<string, unordered_map<string, string>>> decoder_constructors = {
        {"clayg", {}},
        {"clayg", {{"cluster_lifetime", "2"}}},
        {"clayg", {{"stop_early", "true"}}},
        {"clayg", {{"stop_early", "true"}, {"cluster_lifetime", "2"}}},
        {"sl_clayg", {}},
        {"sl_clayg", {{"cluster_lifetime", "2"}}},
        {"sl_clayg", {{"stop_early", "true"}}},
        {"sl_clayg", {{"stop_early", "true"}, {"cluster_lifetime", "2"}}},
        {"uf", {{"stop_early", "true"}}},
        {"uf", {{"stop_early", "false"}}},
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

    shared_ptr<DecodingGraph> decoding_graph = DecodingGraph::repetition_code(D, D);

    logger.set_dump_dir("data/diagrams/explanations");
    logger.set_dump_enabled(true);

    variant<string, int> run_id = "presentation";
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
    bool use_fixed_error_ids = true;
    // read in from file (if present). Format accepted per-line:
    //  - numeric: "<type>-<round>-<id>" e.g. "1-3-24"
    //  - named:   "NORMAL-3-24" or "MEASUREMENT-2-5"
    auto parse_error_id_line = [](const std::string& line) -> std::optional<DecodingGraphEdge::Id> {
        std::string s = line;
        // trim
        s.erase(0, s.find_first_not_of(" \t\r\n"));
        s.erase(s.find_last_not_of(" \t\r\n") + 1);
        if (s.empty()) return std::nullopt;
        if (s[0] == '#') return std::nullopt; // comment

        std::smatch match;
        std::regex re_name(R"(([A-Za-z]+)-(\d+)-(\d+))");
        if (std::regex_search(s, match, re_name)) {
            std::string type_str = match[1].str();
            for (auto &c : type_str) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
            DecodingGraphEdge::Id id{};
            if (type_str == "NORMAL") id.type = DecodingGraphEdge::NORMAL;
            else if (type_str == "MEASUREMENT") id.type = DecodingGraphEdge::MEASUREMENT;
            else return std::nullopt;
            id.round = std::stoi(match[2].str());
            id.id = std::stoi(match[3].str());
            return id;
        }

        return std::nullopt;
    };

    vector<DecodingGraphEdge::Id> fixed_error_ids;
    const std::string fixed_errors_path = "tools/fixed_errors.txt";
    if (use_fixed_error_ids)
    {
        std::ifstream ifs(fixed_errors_path);
        if (ifs)
        {
            std::string line;
            while (std::getline(ifs, line))
            {
                auto maybe_id = parse_error_id_line(line);
                if (maybe_id.has_value())
                    fixed_error_ids.push_back(maybe_id.value());
            }
        }
        else
        {
            // If file not found we proceed with empty list (fall back to random sampling below)
            std::cerr << "Warning: could not open " << fixed_errors_path << "; proceeding without fixed errors\n";
        }
    }

    std::cout << fixed_error_ids.size() << " fixed error IDs loaded.\n";

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
