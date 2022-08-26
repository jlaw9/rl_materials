# Wrapper script to set the run_id and 
# copy the config file to the output directory for a given experiment

# run_id is the first parameter, config is the second
run_id="$1"
config_file="$2"

if [ "$1" == "" ]; then
    echo "Need to pass <run_id> as first argument"
    exit
fi
if [ "$2" == "" ]; then
    echo "Need to pass <config_file> as second argument"
    exit
fi

CONDA_ENV="rl_materials"
#WORKING_DIR="/projects/rlmolecule/$USER/logs/crystal_energy/${run_id}"
WORKING_DIR="outputs/${run_id}"
mkdir -p $WORKING_DIR

# This model was trained on the normalized / scaled structures
ENERGY_MODEL="inputs/models/best_model.hdf5"
# Use this option when the energy model was trained on structures with predicted volume
# so that the input structures will also have their predicted volume applied
#VOL_PRED="--vol-pred-site-bias /projects/rlmolecule/pstjohn/crystal_inputs/site_volumes_from_icsd.csv"

# copy the config file with the rest of the results
SCRIPT_CONFIG="$WORKING_DIR/run.yaml"
cp $config_file $SCRIPT_CONFIG
# also set the run_id in the config file
sed -i "s/crystal_energy_example/$run_id/" $SCRIPT_CONFIG

echo """#!/bin/bash
#SBATCH --account=rlmolecule
#SBATCH --time=4:00:00  
#SBATCH --job-name=$run_id
#SBATCH --mail-type=END
#SBATCH --mail-user=$USER@nrel.gov
#SBATCH --output=$WORKING_DIR/%j-sbatch.out
# --- Policy Trainer ---
#SBATCH --nodes=1
#SBATCH --gres=gpu:2
# --- MCTS Rollouts ---
#SBATCH hetjob
# Use 5 worker nodes for now since we keep hitting the limit of yuma connections
#SBATCH -N 5


# Track which version of the code generated this output
# by putting the current branch and commit in the log file
echo \"Current git branch & commit:\"
git rev-parse --abbrev-ref HEAD
git log -n 1 --oneline

export WORKING_DIR=$WORKING_DIR
mkdir -p $WORKING_DIR
export POLICY_SCRIPT="\$WORKING_DIR/\$JOB/.policy.sh"
export ROLLOUT_SCRIPT="\$WORKING_DIR/\$JOB/.rollout.sh"
# make sure the base folder of the repo is on the python path
export PYTHONPATH="$(readlink -e ../../):\$PYTHONPATH"

cat << EOF > "\$POLICY_SCRIPT"
#!/bin/bash
source $HOME/.bashrc_conda
module use /nopt/nrel/apps/modules/test/modulefiles/
module load cudnn/8.1.1/cuda-11.2
conda activate $CONDA_ENV
python -u optimize_crystal_energy_stability.py \
    --train-policy \
    --config $SCRIPT_CONFIG \
    --energy-model $ENERGY_MODEL \
    $VOL_PRED
EOF

cat << EOF > "\$ROLLOUT_SCRIPT"
#!/bin/bash
source $HOME/.bashrc_conda
module use /nopt/nrel/apps/modules/test/modulefiles/
module load cudnn/8.1.1/cuda-11.2
conda activate $CONDA_ENV
python -u optimize_crystal_energy_stability.py \
    --rollout \
    --config $SCRIPT_CONFIG \
    --energy-model $ENERGY_MODEL \
    $VOL_PRED
EOF


chmod +x "\$POLICY_SCRIPT" "\$ROLLOUT_SCRIPT"

srun --pack-group=0 \
     --job-name="az-policy" \
     --output=$WORKING_DIR/%j-gpu.out \
     "\$POLICY_SCRIPT" &

# there are 36 cores on each eagle node.
# I tried using all 36 cores, 
# but for some reason the node would then only run a couple at a time 
srun --pack-group=1 \
     --ntasks-per-node=17 \
     --cpus-per-task=2 \
     --job-name="az-rollout" \
     --output=$WORKING_DIR/%j-mcts.out \
     "\$ROLLOUT_SCRIPT"
""" > $WORKING_DIR/.submit.sh

echo "sbatch $WORKING_DIR/.submit.sh"
sbatch $WORKING_DIR/.submit.sh
