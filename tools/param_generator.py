import argparse
import ast
import itertools
import json
import os

# --- CLI arguments ---
parser = argparse.ArgumentParser(description="Generate params.txt and update SLURM array job script.")
parser.add_argument("config_file", help="Path to configuration file (e.g. run_config.txt)")
parser.add_argument("--param_file", default="generated_params.txt", help="Path to output parameter file")
parser.add_argument("--batch_file", default="submit_cayg_array_job.sh", help="Path to SLURM batch file")
args = parser.parse_args()

CONFIG_FILE = args.config_file
PARAM_FILE = args.param_file
BATCH_FILE = args.batch_file

# --- Read configuration file ---
with open(CONFIG_FILE, "r") as f:
    config = json.load(f)

print(config)

# --- Helpers ---
def parse_list(val, cast=str):
    return [cast(x.strip()) for x in val.split(",")]

def parse_prob_step(p_step):
    # Normalize step format: "+0.005" or "*1.3" or "/1.3"
    if not p_step:
        return "+0.005"
    if p_step[0] in "+*/":
        return p_step
    if "." in p_step:
        return "+" + p_step
    return p_step

def generate_decoder_string(dec_dict):
    if not isinstance(dec_dict, dict):
        raise ValueError("Decoders must be a dictionary.")

    all_configs = []
    for decoder_name, params in dec_dict.items():
        if not params:  # no parameters
            all_configs.append(f"{decoder_name}()")
            continue

        keys, values = zip(*params.items())
        for combo in itertools.product(*values):
            param_str = ",".join(f"{k}={v}" for k, v in zip(keys, combo))
            all_configs.append(f"{decoder_name}({param_str})")

    return ",".join(all_configs)

# --- Extract configuration ---
distances = config.get("D", [5,7,9,11,13])
rounds_list = config.get("Rounds", [1.0]) # If rounds is specified as a float, treat it as a multiplier of D, otherwise use it directly. 
decoders = generate_decoder_string(config.get("Decoders", {"clayg": {}, "sl_clayg": {}, "uf": {}}))
category = config.get("Category", "new")

mode = config.get("Mode", "").lower()
probabilities_str = config.get("Probabilities", "")
if not mode:
    mode = "probabilities_list" if probabilities_str else "p_step"

probabilities = probabilities_str if probabilities_str else [0.001, 0.005, 0.01]
p_start = float(config.get("P Start", "0.035"))
p_end = float(config.get("P End", "0.0001"))
p_step = parse_prob_step(config.get("P Step", "/1.3"))

idling_start = config.get("Idling Time Constant Start", "0.005")
idling_end = config.get("Idling Time Constant End", "0.01")
idling_step = config.get("Idling Time Constant Step", "+0.005")

runs_p = config.get("Runs_P", "auto")
runs_idling = config.get("Runs_Idling", "auto")
dump = config.get("Dump", "false")
base_dir = config.get("Results Directory", "data/no_category")

# --- Noise models (per-run) ---
# Accept either a comma-separated string or a list in the JSON config under key "Noise Models" or "Noise Model".
raw_noise = config.get("Noise Models", config.get("Noise Model", None))
if raw_noise is None:
    noise_models = ["phenomenological"]
else:
    if isinstance(raw_noise, list):
        noise_models = [str(x) for x in raw_noise]
    else:
        # parse comma-separated string
        noise_models = [x.strip() for x in str(raw_noise).split(",") if x.strip()]
    if not noise_models:
        noise_models = ["phenomenological"]

# parameter ranges
P_MIN, P_MAX = 0.005, 0.02
IDLE_MIN, IDLE_MAX = 200, 1_000_000

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def norm(x, lo, hi):
    return clamp((x - lo) / (hi - lo), 0.0, 1.0)

def auto_runs_p(p, idling):
    # lower p → more runs
    p_norm = norm(p, P_MIN, P_MAX)

    runs_min = 400_000
    runs_max = 1_000_000
        
    # power > 1 gives more weight to small p
    runs = runs_max * (1 - p_norm) ** 2
    return int(clamp(runs, runs_min, runs_max))


def auto_runs_idling(p, idling):
    # lower p and larger idling → more runs
    p_norm = norm(p, P_MIN, P_MAX)
    idling = 1/float(idling) 
    idle_norm = norm(idling, 0, 1/IDLE_MIN)

    runs_min = 1
    runs_max = 500

    difficulty = (1.0 - p_norm) ** 0.8 * idle_norm ** 3
    print(f"p: {p}, idling: {idling}, p_norm: {p_norm}, idle_norm: {idle_norm}, difficulty: {difficulty}")
    runs = runs_min + difficulty * (runs_max - runs_min)

    return int(clamp(runs, runs_min, runs_max))

# selection logic
if runs_p == "auto":
    runs_p = auto_runs_p
else:
    runs_p_const = int(runs_p)
    runs_p = lambda p, idling=None: runs_p_const

if runs_idling == "auto":
    runs_idling = auto_runs_idling
else:
    runs_idling_const = int(runs_idling)
    runs_idling = lambda p, idling: runs_idling_const


# --- Generate params lines ---
lines = []
if mode == "probabilties_list":
    for d in distances:
        for rounds in rounds_list:
            if not type(rounds) == int:
                rounds *= d
            for p in probabilities:
                for noise_model in noise_models:
                    lines.append(
                        f"{decoders} {d} {rounds} {runs_p(p, idling_start)} {runs_idling(p, idling_start)} {base_dir}/{category} {p} {p} *1.2 "
                        f"{idling_start} {idling_end} {idling_step} {noise_model}\n"
                    )
elif mode== "split_p_steps":
        all_ps = []
        p = p_start
        
        apply_step = lambda val, step: (
            val + float(step[1:]) if step[0] == '+'
            else val - float(step[1:]) if step[0] == '-'
            else val * float(step[1:]) if step[0] == '*'
            else val / float(step[1:]) if step[0] == '/'
            else (lambda inv: 1.0 / inv)(1.0 / val + float(step[1:])) if step[0] == '#'
            else (_ for _ in ()).throw(ValueError(f"Unknown step operator: {step[0]}"))
        )

        if p_start > p_end:
            cmp = lambda x: x >= p_end
        else:
            cmp = lambda x: x <= p_end
            
        while cmp(p):
            all_ps.append(p)
            p = apply_step(p, p_step)

        def chunk_list(lst, n):
            for i in range(0, len(lst), n):
                yield lst[i], lst[min(len(lst)-1, i+n-1)]
            
        chunks = list(chunk_list(all_ps, 2))

        for d in distances:
            for rounds in rounds_list:
                if not type(rounds) == int:
                    rounds *= d
                for chunk_start, chunk_end in chunks:
                    for noise_model in noise_models:
                        lines.append(
                            f"{decoders} {d} {rounds} {runs_p(chunk_start, idling_start)} {runs_idling(chunk_start, idling_start)} {base_dir}/{category} {chunk_start} {chunk_end} {p_step} "
                            f"{idling_start} {idling_end} {idling_step} {noise_model}\n"
                        )
elif mode == "p_step":
    for d in distances:
        for rounds in rounds_list:
            if not type(rounds) == int:
                rounds *= d
        for noise_model in noise_models:
            lines.append(
                f"{decoders} {d} {rounds} {runs_p(p_start, idling_start)} {runs_idling(p_start, idling_start)} {base_dir}/{category} {p_start} {p_end} {p_step} "
                f"{idling_start} {idling_end} {idling_step} {noise_model}\n"
            )
else:
    raise ValueError(f"Unknown mode: {mode}")

# --- Write params.txt ---
with open(PARAM_FILE, "w") as f:
    f.writelines(lines)

# --- Update SLURM array size ---
num_lines = len(lines)
array_line = f"#SBATCH --array=0-{num_lines-1} # Set to number of lines in params.txt minus 1"
param_file_line = f"PARAM_FILE=\"$CWD/{PARAM_FILE}  # Path to the parameter file"

with open(BATCH_FILE, "r") as f:
    bash_lines = f.readlines()

for i, l in enumerate(bash_lines):
    if l.strip().startswith("#SBATCH --array="):
        bash_lines[i] = array_line + "\n"
        break
    if l.strip().startswith("PARAM_FILE="):
        bash_lines[i] = param_file_line + "\n"
        break

with open(BATCH_FILE, "w") as f:
    f.writelines(bash_lines)

print(f"Generated {PARAM_FILE} with {num_lines} lines.")
print(f"Updated {BATCH_FILE} with array range 0-{num_lines-1}.")