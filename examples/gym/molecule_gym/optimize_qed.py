import tensorflow as tf

gpus = tf.config.list_physical_devices('GPU')
if gpus:
    for gpu in gpus:
        tf.config.experimental.set_memory_growth(gpu, True)

import argparse
import os

import ray
from ray import tune
from ray.rllib.models import ModelCatalog
from ray.tune.registry import register_env


def env_creator(args):

    if isinstance(args, argparse.Namespace):
        args = vars(args)

    print("env_creator args", args)

    import tensorflow as tf

    gpus = tf.config.list_physical_devices('GPU')
    if gpus:
        for gpu in gpus:
            tf.config.experimental.set_memory_growth(gpu, True)

    from examples.gym.molecule_gym.molecule_graph_problem import QEDGraphProblem
    from rlmolecule.graph_gym.graph_gym_env import GraphGymEnv
    from rlmolecule.molecule.builder.builder import MoleculeBuilder

    env = GraphGymEnv(
            QEDGraphProblem(
                MoleculeBuilder(
                    max_atoms=args["max_atoms"],
                    min_atoms=args["min_atoms"],
                    sa_score_threshold=args["sa_score_threshold"],
                    atom_additions=['C', 'N', 'O', 'S'],
                    stereoisomers=False,
                    try_embedding=True,
                ),
                max_num_bonds=args["max_num_bonds"],
                max_num_actions=args["max_num_actions"]
            )
    )

    return env

from rlmolecule.graph_gym.graph_gym_model import GraphGymModel
    
class ThisModel(GraphGymModel):
    def __init__(self,
                    obs_space,
                    action_space,
                    num_outputs,
                    model_config,
                    name,
                    **kwargs):

        import tensorflow as tf
        gpus = tf.config.list_physical_devices('GPU')
        if gpus:
            for gpu in gpus:
                tf.config.experimental.set_memory_growth(gpu, True)

        from rlmolecule.molecule.policy.model import policy_model

        super(ThisModel, self).__init__(
            obs_space, action_space, num_outputs, model_config, name,
            policy_model(
                features=8,
                num_heads=2,
                num_messages=1
            )
        )


if __name__ == "__main__":

    from optimize_qed_argparser import parser

    args = parser.parse_args()
    print("ARGS", args)

    print ("ray initializing")
    if args.redis_password is None:
        ray.init()
    else:
        ray.init(_redis_password=args.redis_password, address=os.environ["ip_head"])
    print ("ray initialized")

    example_env = env_creator(args)


    register_env("molecule_graph_problem", env_creator)

    ModelCatalog.register_custom_model('molecule_graph_problem_model', ThisModel)

    if args.run == 'DQN':
        cfg = {
            # TODO(ekl) we need to set these to prevent the masked values
            # from being further processed in DistributionalQModel, which
            # would mess up the masking. It is possible to support these if we
            # defined a custom DistributionalQModel that is aware of masking.
            'hiddens': [],
            'dueling': False,
        }
    else:
        cfg = {}

    num_workers = args.num_cpus
    rollout_fragment_length = args.max_atoms
    train_batch_size = num_workers * rollout_fragment_length
    config = dict(
        {
            'env': "molecule_graph_problem",
            "env_config": vars(args),
            'model': {
                'custom_model': 'molecule_graph_problem_model',
            },
            'num_gpus': args.num_gpus,
            'num_workers': num_workers,
            'framework': 'tf2',
            'eager_tracing': False,   # does not work otherwise?
            # "lr": tune.grid_search([1e-2]),
            "lr_schedule": [[0, 1e-2], [400000, 1e-3], [800000, 1e-4]],
            "gamma": 1.0,  # finite horizon problem, we want total reward-to-go?
            "entropy_coeff": tune.grid_search([0.05]),
            "num_sgd_iter": tune.grid_search([10]),
            'rollout_fragment_length': rollout_fragment_length,
            'train_batch_size': train_batch_size,
            # sgd_minibatch_size needs to be carefully tuned for the problem, 
            # too small and it slows down the training iterations dramatically
            'sgd_minibatch_size': min(train_batch_size, 500),
            "batch_mode": 'complete_episodes', 
            "log_level": args.log_level.upper()
        },
        **cfg)

    print("CONFIG", config)

    stop = {
        'training_iteration': args.stop_iters,
        'timesteps_total': args.stop_timesteps,
        'episode_reward_mean': args.stop_reward,
    }

    experiment = tune.run(
        args.run,
        config=config,
        stop=stop,
        num_samples=args.num_samples,
        checkpoint_freq=1,
        checkpoint_at_end=True,
        checkpoint_score_attr="episode_reward_mean",
        keep_checkpoints_num=100,
        local_dir=args.local_dir,
        verbose=3
    )

    ray.shutdown()

