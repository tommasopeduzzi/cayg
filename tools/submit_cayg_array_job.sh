#!/bin/bash
#SBATCH --job-name=cayg_array
#SBATCH --output=stdout/slurm-%x-%A_%a.out
#SBATCH --error=stderr/slurm-%x-%A_%a.err
#SBATCH --time=28:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --array=0-27 # Set to number of lines in params.txt minus 1

CWD="$(pwd)"
CAYG_EXEC="$CWD/../build/cayg"
PARAM_FILE="$CWD/generated_params.txt"

LINE=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "$PARAM_FILE")
read -r DECODERS DIST ROUNDS RUNS_P RUNS_IDLING BASE_DIR P_START P_END P_STEP IDL_START IDL_END IDL_STEP NOISE_MODEL <<< "$LINE"
echo "$LINE"

timestamp=$(date +%F_%H-%M-%S)
noise_dir=$(echo "${NOISE_MODEL}" | sed 's/[^A-Za-z0-9._-]/_/g')
OUTPUT_DIR="$CWD/../$BASE_DIR/${timestamp}_D${DIST}/${noise_dir}"
mkdir -p "$OUTPUT_DIR"

echo "[$SLURM_ARRAY_TASK_ID] D=$DIST, T=$ROUNDS, (prob $P_START→$P_END $P_STEP) (idling $IDL_START→$IDL_END $IDL_STEP) noise_model=$NOISE_MODEL -> $OUTPUT_DIR"

"$CAYG_EXEC" "$DIST" "$ROUNDS" "$DECODERS" "$OUTPUT_DIR" \
    --p_start "${P_START}" --p_end "${P_END}" --p_step "${P_STEP}" \
    --idling_time_constant_start "$IDL_START" --idling_time_constant_end "$IDL_END" --idling_time_constant_step "$IDL_STEP" \
    --dump false --runs_p "$RUNS_P" --runs_idling "$RUNS_IDLING" \
    --noise_model "$NOISE_MODEL"
