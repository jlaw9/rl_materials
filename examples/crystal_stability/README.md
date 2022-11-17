## Prerequisites
First, a model for predicting the total energy of a structure is required.
Download the model used in the manuscript from here: https://github.com/jlaw9/upper-bound-energy-gnn 
and place it in the folder `inputs/models`

A `yaml` config file specifies the prototypes, action graph, RL hyperparameters, and environment-specific variables 
such as the sql database for storing and loading the state rewards from the rollouts. See below for more details.

## Generate action graph
- The action space for designing crystal structures is based on two action graphs. 
  1. elements to compositions
  2. composition type to decorations
- Although we cannot provide the prototype ICSD structures, the action graphs we used are available here:
  1. `../../rlmolecule/crystal/inputs/eles_to_comps.edgelist.gz`
  2. `../../rlmolecule/crystal/inputs/comp_type_to_decors.edgelist.gz`

- To recreate the action graphs, you must gain access to and download the ICSD structures. 
  The list of ICSD structure IDs we used are here: `rlmolecule/crystal/inputs/prototypes.csv`
  - We also included 85 structures as prototypes based on a clustering of the structures our fully-relaxed dataset.
    Those structures are here: `rlmolecule/crystal/inputs/fully_relaxed_clustered_prototypes.json.gz`

Example call to build the action space:
```
python scripts/build_action_space.py \
    --prototypes-json ../../rlmolecule/crystal/inputs/icsd_prototypes.json.gz \
    --prototypes-json ../../rlmolecule/crystal/inputs/fully_relaxed_clustered_prototypes.json.gz \
    --out-pref ../../rlmolecule/crystal/inputs/ \
    --write-proto-json \
    --enumerate-decorations \
    > ../../rlmolecule/crystal/inputs/action_space_log.txt
```

## Run the RL optimization locally
To run rollout workers (on a CPU): 
```
python optimize_crystal_energy_stability.py \
    --config config/config_local.yaml \
    --energy-model inputs/models/best_model.hdf5  \
    --rollout
```

To train the policy model (on a GPU):
```
python optimize_crystal_energy_stability.py \
    --config config/config_local.yaml \
    --energy-model inputs/models/best_model.hdf5  \
    --train-policy
```

Note that the policy model requires enough completed rollouts 
(specified in the config file) to start training. 
The rollout workers will automatically check for and load the latest
policy model trained so far at the start of each rollout.

## Run the RL optimization on NREL's Eagle HPC system
To start the optimization process with 5 rollout nodes (17 workers on each = 85 total workers) and 1 policy training GPU node:
```
# specify a name for this run, and the config file to use
bash run_eagle.sh 20220826-optimize-batt-reward config/config_for_paper.yaml
```

Note that currently each policy model is stored, which can take up a lot of space. 
Change the `WORKING_DIR` variable in `run_eagle.sh` and the `policy_checkpoint_dir` flag in the config file
to specify where the outputs will be written.

On eagle, I ran into issues where the rollout workers would get stuck waiting to load the states already seen 
and the policy model. I made the scripts `copy_policy.sh` and `run_eagle_copy_policy.sh` to get around the issue.
Run the same as above:
```
bash run_eagle_copy_policy.sh 20220826-optimize-batt-reward config/config_for_paper.yaml
```

## Run the RL optimization on a different HPC system
The main difference will be the database used. 
Update the variables under `sql_database` in the config file to use your system's SQL database.
If the new HPC system also uses slurm and has GPU nodes, then much of the `run_eagle.sh` script should be transferrable.
Note that a GPU node for the policy model is not required, it just makes training faster.
