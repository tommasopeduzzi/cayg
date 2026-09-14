# cayg

[![DOI](https://zenodo.org/badge/753687432.svg)](https://doi.org/10.5281/zenodo.22008965)

Simulation and decoding code for the paper **"Reducing Decoding Latency in Quantum Error Correction by Early Starting Clustering"**.

The project implements CAYG (a during-measurement clustering decoder) alongside a Union-Find baseline and a Peeling decoder for the rotated surface code, and provides tooling to run large-scale Monte Carlo simulations and turn the results into the paper's figures.

## Repository structure

- **`src/`, `include/`** — Core C++ simulation and decoding library: the decoding graph, cluster growth, the CAYG/Union-Find/Peeling decoders, logical error computation, and the `cayg` command-line executable (`src/main.cpp`) that runs the Monte Carlo sweeps.
- **`tools/`** — Python and C++ utilities built around the core library:
  - `figures/` — Jupyter notebook(s) and cached data used to produce the paper's plots.
  - `param_generator.py`, `submit_cayg_array_job.sh` — generate parameter sweeps and submit them as SLURM array jobs.
  - `decoding_graph_renderer.py`, `diagram_data_generator.cpp`, `surface_code_check.cpp` — visualization and sanity-check helpers.
- **`data/`** — Simulation output (results, logs, generated data), organized by experiment.

## Building

```sh
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

This produces the `cayg` executable along with the `diagram_data_generator` and `surface_code_check` helper binaries. The project has no external dependencies beyond a C++20 standard library.

## Running

```sh
./build/cayg <distance> <rounds> <decoders> <results_dir> [options...]
```

See `src/main.cpp` for the full list of options (probability sweep, decoder parameters, noise model, idling time constants, etc.).
