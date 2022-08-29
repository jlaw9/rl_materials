# rl_materials

## About

A library for general-purpose material optimization using AlphaZero-style reinforcement learning.

This repo is a snapshot of the code used for the paper "Upper-Bound Energy Minimization to Search for Stable
Functional Materials with Graph Neural Networks" in 2022.  

The code to optimize crystal structures for thermodynamic stability is in the folder `examples/crystal_stability`.
Please see [this README](https://github.com/jlaw9/rlmolecule/tree/crystal_reward/examples/crystal_stability) for more details.

This library was first used for molecule optimization, hence the package name `rlmolecule`. 
The molecule optimization code has been removed. Please see [rlmolecule](https://github.com/NREL/rlmolecule)
if interested.

Some of this code is specific to the HPC system 
available at NREL which relies on the SQL database called "Yuma". The README linked to above has instructures to modify the code
to run in different environments.

We are actively developing the [graphenv](https://github.com/NREL/graph-env) library 
which is much more general purpose and enables scalability via the popular RLLib library. 
We will soon update this README with a link to an updated version of this repo that uses `graphenv`.




## Installation

Most dependencies for this project are installable via conda, with the exception of [nfp](https://github.com/NREL/nfp),
which can be installed via pip. An example conda environment file is provided below:

```yaml
channels:
  - conda-forge
  - defaults
  
dependencies:
  - python=3.7
  - jupyterlab
  - seaborn
  - pandas
  - scikit-learn
  - jupyter
  - notebook
  - pymatgen
  - xlrd
  - tqdm
  - tensorflow-gpu
  - psycopg2
  - pip
    - pip:
    - nfp >= 0.1.4
```

