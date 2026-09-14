#include <iostream>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <format>
#include <random>
#include <regex>
#include <unordered_map>
#include <unordered_set>

#include "DecodingGraph.h"
#include "UnionFindDecoder.h"
#include "CAYGDecoder.h"
#include "Logger.h"
#include "LogicalComputer.h"
#include "ParsingUtils.h"

using namespace std;

struct ArgSpec {
    string name;
    string default_value;
    function<void(const string&)> validator;
};

struct DecoderConfig {
    string name;
    unordered_map<string, string> args;
};

static bool is_likely_decoder_arg_key(const string& key)
{
    if (key.empty()) return false;
    if (!(islower(static_cast<unsigned char>(key[0])) || key[0] == '_')) return false;
    for (char c : key) {
        if (!(islower(static_cast<unsigned char>(c)) || isdigit(static_cast<unsigned char>(c)) || c == '_'))
            return false;
    }
    return true;
}

vector<DecoderConfig> parse_decoder_list(const string& input) {
    vector<DecoderConfig> decoders;
    size_t pos = 0;
    while (pos < input.size()) {
        size_t start = pos;
        size_t paren = input.find('(', start);
        size_t comma = input.find(',', start);
        DecoderConfig cfg;

        if (paren != string::npos && (comma == string::npos || paren < comma)) {
            cfg.name = input.substr(start, paren - start);
            size_t close = input.find(')', paren);
            if (close == string::npos)
                throw invalid_argument("Missing ')' in decoder list for: " + cfg.name);

            string inside = input.substr(paren + 1, close - paren - 1);
            vector<pair<string, string>> key_values;
            try {
                key_values = ParsingUtils::parse_key_value_list(inside);
            } catch (const invalid_argument& e) {
                throw invalid_argument("Invalid decoder arg in " + cfg.name + ": " + e.what());
            }

            string previous_key;
            for (const auto& [key, value] : key_values) {
                // FIXME: This is stupid.
                if (!is_likely_decoder_arg_key(key) && !previous_key.empty()) {
                    cfg.args[previous_key] += "," + key + "=" + value;
                    continue;
                }
                cfg.args[key] = value;
                previous_key = key;
            }
            pos = close + 1;
        } else {
            size_t end = (comma == string::npos) ? input.size() : comma;
            cfg.name = input.substr(start, end - start);
            pos = end;
        }

        while (pos < input.size() && (input[pos] == ',' || isspace(input[pos])))
            ++pos;

        if (!cfg.name.empty())
            decoders.push_back(cfg);
    }
    return decoders;
}

unordered_map<string, string> parse_args(int argc, char* argv[]) {
    vector<ArgSpec> specs = {
        {"D", "", [](const string& v){ stoi(v); }},
        {"T", "", [](const string& v){ stoi(v); }},
        {"decoders", "", [](const string& v){
            if (v.empty()) throw invalid_argument("empty decoders");
            parse_decoder_list(v);
        }},
        {"results", "", [](const string& v){ if (v.empty()) throw invalid_argument("empty results path"); }},
        {"p_start", "0.005", [](const string& v){ stod(v); }},
        {"p_end", "0.005", [](const string& v){ stod(v); }},
        {"p_step", "+0.005", [](const string& v){
            if (v.size() < 2 || string("+-*/").find(v[0]) == string::npos)
                throw invalid_argument("must start with +, -, *, or /");
            stod(v.substr(1));
        }},
        {"idling_time_constant_start", "0.0", [](const string& v){ stod(v); }},
        {"idling_time_constant_end", "0.0", [](const string& v){ stod(v); }},
        {"idling_time_constant_step", "+0.0", [](const string& v){
            if (v.size() < 2 || string("+-*/").find(v[0]) == string::npos)
                throw invalid_argument("must start with +, -, *, or /");
            stod(v.substr(1));
        }},
        {"dump", "false", [](const string& v){
            if (v != "true" && v != "false")
                throw invalid_argument("must be true or false");
        }},
        {"runs_p", "10000", [](const string& v){ stoi(v); }},
        {"runs_idling", "10000", [](const string& v){ stoi(v); }},
        {"noise_model", "phenomenological", [](const string& v){ /* free-form; parsed later */ }},
    };

    unordered_set<string> known_keys;
    for (auto& s : specs) known_keys.insert(s.name);

    unordered_map<string, string> args;
    vector<string> positional = {"D", "T", "decoders", "results"};
    int pos_i = 0;

    for (int i = 1; i < argc; ++i) {
        string token = argv[i];

        // Option-style argument
        if (token.rfind("--", 0) == 0) {
            string key = token.substr(2);
            if (!known_keys.count(key)) {
                cerr << "Unknown option: " << key << endl;
                exit(1);
            }

            if (i + 1 >= argc) {
                cerr << "Missing value for option: " << key << endl;
                exit(1);
            }

            string value = argv[++i];
            args[key] = value;
        }
        // Positional arguments (D, T, decoders, results)
        else if (pos_i < (int)positional.size()) {
            args[positional[pos_i++]] = token;
        } else {
            cerr << "Unexpected argument: " << token << endl;
            exit(1);
        }
    }

    // Apply defaults & validate
    for (const auto& spec : specs) {
        if (args.find(spec.name) == args.end())
            args[spec.name] = spec.default_value;

        try {
            spec.validator(args[spec.name]);
        } catch (const invalid_argument& e) {
            cerr << "Invalid argument for " << spec.name << ": " << args[spec.name]
                 << "\nReason: " << e.what() << endl;
            exit(1);
        }
    }
    return args;
}

// Parse a noise model string (e.g. "NORMAL=1,MEASUREMENT=0.1" or "phenomenological")
// into a map from DecodingGraphEdge::Type -> factor.
static std::map<DecodingGraphEdge::Type, double> parse_noise_model(const string& noise_model_arg)
{
    std::map<DecodingGraphEdge::Type, double> type_factors;
    if (noise_model_arg.empty() || noise_model_arg == "phenomenological") {
        type_factors[DecodingGraphEdge::NORMAL] = 1.0;
        type_factors[DecodingGraphEdge::MEASUREMENT] = 1.0;
        return type_factors;
    }

    // defaults
    double normal_val = NAN;
    double measurement_val = NAN;

    vector<pair<string, string>> key_values;
    try {
        key_values = ParsingUtils::parse_key_value_list(noise_model_arg);
    } catch (const std::invalid_argument& e) {
        cerr << "Invalid noise_model: " << e.what() << endl;
        exit(1);
    }

    for (auto& [key, val] : key_values) {
        for (auto & c: key) c = static_cast<char>(toupper(static_cast<unsigned char>(c)));
        try {
            if (key == "NORMAL") {
                normal_val = stod(val);
            } else if (key == "MEASUREMENT") {
                measurement_val = stod(val);
            } else {
                cerr << "Unknown edge type in noise_model: " << key << endl;
                exit(1);
            }
        } catch (const std::exception& e) {
            cerr << "Invalid numeric value in noise_model for " << key << ": " << val << endl;
            exit(1);
        }
    }

    if (std::isnan(normal_val)) normal_val = 1.0;
    if (std::isnan(measurement_val)) measurement_val = 1.0;
    type_factors[DecodingGraphEdge::NORMAL] = normal_val;
    type_factors[DecodingGraphEdge::MEASUREMENT] = measurement_val;
    return type_factors;
}

vector<DecodingGraphEdge::Id> parse_errors(string errors)
{
    vector<DecodingGraphEdge::Id> error_edge_ids;
    regex re(R"((\d+)-(\d+)-(\d+))");
    smatch match;
    istringstream stream(errors);
    while (getline(stream, errors))
    {
        if (regex_search(errors, match, re))
        {
            DecodingGraphEdge::Id id{};
            id.type = static_cast<DecodingGraphEdge::Type>(stoi(match[1].str()));
            id.round = stoi(match[2].str());
            id.id = stoi(match[3].str());
            error_edge_ids.push_back(id);
        }
        // check if line is not only whitespace
        else {
            regex re_ws(R"(^\s*$)");
            if (!regex_match(errors, re_ws))
             {
                 cerr << "Error: Invalid error format: " << errors << endl;
                 exit(1);
             }
        }
    }
    return error_edge_ids;
}

int compute_logical(const shared_ptr<DecodingGraph>& decoding_graph,
                    const vector<shared_ptr<DecodingGraphEdge>>& bulk_errors,
                    const vector<shared_ptr<DecodingGraphEdge>>& idling_errors,
                    const DecodingResult& decoding_result)
{
    int consider_errors_up_to_round = decoding_result.considered_up_to_round;
    auto correction_edges = decoding_result.corrections;

    map<int, bool> final_measurement;

    for (auto& edge : decoding_graph->edges())
    {
        final_measurement[edge->id().id] = false;
    }
    const vector<const vector<shared_ptr<DecodingGraphEdge>>*> flip_sets = {&bulk_errors, &correction_edges, &idling_errors};
    for (const auto* edges_to_flip : flip_sets)
    {
        for (const auto& edge : *edges_to_flip)
        {
            if (edge->id().round > consider_errors_up_to_round)
                continue;
            final_measurement[edge->id().id] = !final_measurement[edge->id().id];
        }
    }

    auto final_correction_decoding_graph = DecodingGraph::single_layer_copy(decoding_graph);
    for (const auto& node : final_correction_decoding_graph->nodes())
    {
        // FIXME: Maybe even mark virtual nodes?
        if (node->id().type == DecodingGraphNode::Type::VIRTUAL)
            continue;
        bool defect = false;
        for (const auto& edge_weak_ptr : node->edges())
        {
            const auto edge = edge_weak_ptr.lock();
            defect = defect xor final_measurement[edge->id().id];
        }
        node->set_marked(defect);
    }
    auto final_classical_correction = UnionFindDecoder().decode(final_correction_decoding_graph);
    for (auto& edge : final_classical_correction.corrections)
    {
        final_measurement[edge->id().id] = !final_measurement[edge->id().id];
    }

    bool logical = false;
    for (auto logical_edge : decoding_graph->logical_edge_ids())
    {
        logical = logical xor final_measurement[logical_edge];
    }
    return logical;
}

void increment_by_step(double& variable, pair<char, double> step)
{
    switch (step.first)
    {
    case '+':
        variable += step.second;
        break;
    case '/':
        variable /= step.second;
        break;
    case '-':
        variable -= step.second;
        break;
    case '*':
        variable *= step.second;
        break;

    case '#': // harmonic scaling (linear in 1/x)
        {
            double inv = 1.0 / variable;
            inv += step.second;
            variable = 1.0 / inv;
        }
        break;
    default:
        throw std::invalid_argument("Unknown step type");
    }
}

bool increment_end_condition(double const current_variable, double start, double end)
{
    if (start < end)
        return current_variable > end;
    if (start > end)
        return current_variable < end;
    return true;
}

int main(int argc, char* argv[])
{
    // Parse command line arguments in format D T p_start p_end decoders results [step] [dump] [runs]
    auto args = parse_args(argc, argv);

    int D = stoi(args["D"]);
    int T = stoi(args["T"]);
    // parse noise model string into per-type factors
    auto noise_model = parse_noise_model(args["noise_model"]);

    double p_start = stod(args["p_start"]);
    double p_end = stod(args["p_end"]);
    pair p_step = {args["p_step"][0], stod(args["p_step"].substr(1))};
    double idling_time_constant_start = stod(args["idling_time_constant_start"]);
    double idling_time_constant_end = stod(args["idling_time_constant_end"]);
    pair idling_time_constant_step = {args["idling_time_constant_step"][0], stod(args["idling_time_constant_step"].substr(1))};
    string results_file_path = args["results"];
    bool dump = string(args["dump"]) == "true";
    logger.set_dump_enabled(dump);
    int runs_p = stoi(args["runs_p"]);
    int runs_idling = stoi(args["runs_idling"]);

    // Parse decoders argument (comma-separated)
    vector<string> decoder_names;
    string decoders_arg = args["decoders"];
    auto parsed_decoders = parse_decoder_list(decoders_arg);

    // Instantiate decoders
    vector<shared_ptr<Decoder>> decoders;
    for (auto& [decoder_name, decoder_args] : parsed_decoders) {
        if (decoder_name == "uf" || decoder_name == "unionfind") {
            decoders.push_back(make_shared<UnionFindDecoder>(decoder_args));
        } else if (decoder_name == "clayg") {
            decoders.push_back(make_shared<CAYGDecoder>(decoder_args));
        } else if (decoder_name == "single_layer_clayg" || decoder_name == "sl_clayg") {
            decoders.push_back(make_shared<SingleLayerCAYGDecoder>(decoder_args));
        } else {
            cerr << "Unknown decoder: " << decoder_name << endl;
            exit(1);
        }
    }

    logger.set_results_dir(args["results"]);
    logger.set_distance(D);
    logger.set_rounds(T);

    auto graph = DecodingGraph::rotated_surface_code(D, T);
    auto logical_computer = LogicalComputer(graph);

    vector<shared_ptr<DecodingGraphEdge>> error_edges{};
    vector<DecodingGraphEdge::Id> error_edge_ids{};

    random_device rd;
    mt19937 gen(rd());
    uniform_real_distribution<> dis(0, 1);

    double p = p_start;

    vector<pair<double, map<string, double>>> results;
    static auto last_three_runs_corrected = [&] ()
    {
        return std::size(results) >= 3 &&
            std::all_of(results.end() - 3, results.end(), [&](const auto& run) {
                const auto& results_per_decoder = run.second;
                return std::all_of(decoders.begin(), decoders.end(), [&](const auto& decoder) {
                    return results_per_decoder.at(decoder->decoder_name()) == 0;
                });
            });
    };

    do
    {
        struct stats {
            double rolling_sum = 0.0;
            double sum_sq = 0.0;
            int count = 0;
        };
        // decoder name -> idling time constant -> total logical errors
        map<string, map<double, stats>> errors;
        // decoder nme -> idling time constant -> (p_idling_sum, count)
        map<string, map<double, stats>> idling;
        map<string, map<double, int>> growth_steps;
        for (const auto& decoder : decoders) {
            errors[decoder->decoder_name()] = {};
            // always compute for idling time constant 0.0 for last three corrected runs condition
            errors[decoder->decoder_name()][0.0] = {};
            auto idling_time_constant = idling_time_constant_start;
            while (!increment_end_condition(idling_time_constant, idling_time_constant_start, idling_time_constant_end))
            {
                errors[decoder->decoder_name()][idling_time_constant] = {};
                idling[decoder->decoder_name()][idling_time_constant] = {};
                increment_by_step(idling_time_constant, idling_time_constant_step);
            }
            growth_steps[decoder->decoder_name()] = {};
        }

        const std::string run_id_prefix = "d=" + std::to_string(D) + "_p=" + format("{:.5f}", p) + "_run=";
        int dumped_runs = 0;
        logger.set_run_id(run_id_prefix + std::to_string(dumped_runs));
        for (int i = 0; i < runs_p; i++)
        {
            logger.prepare_dump_dir();
            error_edge_ids = graph->sample_errors(p, noise_model, T, dis, gen);
            logger.log_errors(error_edge_ids);
            logger.log_graph(graph);
            error_edges.clear();
            for (auto id : error_edge_ids)
            {
                auto edge = graph->edge(id).value();
                error_edges.push_back(edge);
            }
            bool uncorrected = false;
            std::map<string, bool> corrected_per_decoder {};
            for (const auto& decoder : decoders) {
                graph->reset();
                graph->mark(error_edges);
                auto decoding_results = decoder->decode(graph);
                vector<DecodingGraphEdge::Id> correction_ids;
                for (auto& edge : decoding_results.corrections) {
                    correction_ids.push_back(edge->id());
                }
                logger.log_corrections(correction_ids, decoding_results.correction_steps, decoder->decoder_name());

                logical_computer.clear_cache();
                int logical_without_idling = logical_computer.compute(error_edges, {}, decoding_results);
                if (logical_without_idling != 0) {
                    uncorrected = true;
                }
                corrected_per_decoder[decoder->decoder_name()] = (logical_without_idling == 0);
                errors[decoder->decoder_name()][0.0].rolling_sum += logical_without_idling;
                errors[decoder->decoder_name()][0.0].sum_sq += logical_without_idling * logical_without_idling;
                errors[decoder->decoder_name()][0.0].count += 1;

                double num_growth_steps = decoding_results.decoding_steps;
                growth_steps[decoder->decoder_name()][num_growth_steps] += 1;

                double idling_time_constant = idling_time_constant_start;
                while (!increment_end_condition(idling_time_constant, idling_time_constant_start, idling_time_constant_end))
                {
                    if (idling_time_constant == 0.0) // already computed above
                    {
                        increment_by_step(idling_time_constant, idling_time_constant_step);
                        continue;
                    }
                    double p_idling = 0.5 * (1-exp(-(num_growth_steps/idling_time_constant)));
                    idling[decoder->decoder_name()][idling_time_constant].rolling_sum += p_idling;
                    idling[decoder->decoder_name()][idling_time_constant].count += 1;
                    double history_idling_failures = 0.0;
                    for (int run_idling = 0; run_idling < runs_idling; run_idling++)
                    {
                        auto idling_error_edge_ids = graph->sample_errors(p_idling, noise_model, 1, dis, gen);
                        auto idling_error_edges = vector<shared_ptr<DecodingGraphEdge>>{};
                        for (auto id : idling_error_edge_ids)
                        {
                            auto edge = graph->edge(id).value();
                            idling_error_edges.push_back(edge);
                        }
                        int logical_after_idling = logical_computer.compute(error_edges, idling_error_edges,
                            decoding_results);
                        history_idling_failures += logical_after_idling;
                    }
                    errors[decoder->decoder_name()][idling_time_constant].rolling_sum += history_idling_failures;
                    errors[decoder->decoder_name()][idling_time_constant].sum_sq += history_idling_failures * history_idling_failures;
                    errors[decoder->decoder_name()][idling_time_constant].count += runs_idling;
                    increment_by_step(idling_time_constant, idling_time_constant_step);
                }
            }
            if (uncorrected)
            {
                dumped_runs += 1;
                logger.set_run_id(run_id_prefix + std::to_string(dumped_runs));
            }
            Logger::log_progress(i + 1, runs_p, p, D);
        }
        // Log results and average growth steps for each decoder
        results.push_back({p, {}});
        for (const auto& decoder : decoders) {
            for (const auto& [idling_time_constant, stats] : errors[decoder->decoder_name()]) {
                double logical_error_rate = static_cast<double>(stats.rolling_sum) / stats.count;
                logger.log_results_entry(logical_error_rate, stats.count, stats.sum_sq, p,  idling_time_constant, decoder->decoder_name());
                if (idling_time_constant == 0.0)
                    results.back().second[decoder->decoder_name()] = logical_error_rate;
            }
            for (const auto& [idling_time_constant, stats] : idling[decoder->decoder_name()]) {
                double average_p_idling = stats.rolling_sum / stats.count;
                logger.log_idling_entry(average_p_idling, stats.count, p, idling_time_constant, decoder->decoder_name());
            }
            logger.log_growth_steps(p, growth_steps[decoder->decoder_name()], decoder->decoder_name());
        }
        increment_by_step(p, p_step);
    } while (!increment_end_condition(p, p_start, p_end) || last_three_runs_corrected());
    return 0;
}
