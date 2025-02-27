import logging
import sys
import time
from typing import Tuple, Optional

import numpy as np
from sqlalchemy import create_engine

from gridworld_env import GridWorldEnv as GridEnv
from gridworld_env import make_empty_grid
from rlmolecule.alphazero.tensorflow.tfalphazero_problem import TFAlphaZeroProblem
from rlmolecule.gym.alphazero_gym import AlphaZeroGymEnv
from rlmolecule.gym.gym_problem import GymProblem
from rlmolecule.gym.gym_state import GymEnvState
from rlmolecule.tree_search.reward import LinearBoundedRewardFactory
from tf_model import gridworld_scalar_policy as policy

logger = logging.getLogger(__name__)


# NOTE: These class definitions need to stay outside of construct_problem
# or you will error out on not being able to pickle/serialize them.
class GridWorldEnv(AlphaZeroGymEnv):
    def reset(self):
        return self.env.reset()

    def get_obs(self) -> np.ndarray:
        return self.env.make_observation()


class GridWorldProblem(GymProblem, TFAlphaZeroProblem):

    def policy_model(self) -> "tf.keras.Model":
        size = self.env.size
        # last_filter = int(size/8)
        # return policy(
        #     size=size,
        #     filters=[4, 8, 16, 32],
        #     kernel_size=[2, 2, 2, last_filter],
        #     strides=[2, 2, 2, 1],
        #     hidden_layers=2,
        #     hidden_dim=256
        # )
        return policy(size)

    def get_policy_inputs(self, state: GymEnvState) -> dict:
        return {
            "obs": state.env.make_observation(),
            # "steps": np.array([np.float64(self.env.episode_steps / self.env.max_episode_steps)])
        }

    def get_reward(self, state: GymEnvState) -> Tuple[float, dict]:
        return state.cumulative_reward, {}

    def get_policy_mask(self) -> {str: Optional[float]}:
        initial_inputs = self.get_policy_inputs(self.get_initial_state())  # numpy
        model_inputs = self.policy_model().inputs  # keras
        for (x, y) in zip(initial_inputs.values(), model_inputs):
            types = (x.dtype, y.dtype.as_numpy_dtype)
            if not np.issubdtype(*types):
                raise TypeError("State and policy input types must match, got", types)
        return {key: np.array(-1, dtype=val.dtype) for key, val in initial_inputs.items()}


def construct_problem(size):
    # engine = create_engine(f'sqlite:///gridworld_data.db',
    #                        connect_args={'check_same_thread': False},
    #                        execution_options = {"isolation_level": "AUTOCOMMIT"})

    dbname = "bde"
    port = "5432"
    host = "yuma.hpc.nrel.gov"
    user = "rlops"
    passwd_file = '/projects/rlmolecule/rlops_pass'
    with open(passwd_file, 'r') as f:
        passwd = f.read().strip()

    drivername = "postgresql+psycopg2"
    engine_str = f'{drivername}://{user}:{passwd}@{host}:{port}/{dbname}'
    engine = create_engine(engine_str, execution_options={"isolation_level": "AUTOCOMMIT"})

    grid = make_empty_grid(size=size)
    env = GridEnv(grid, observation_type="scalar", max_episode_steps=2 * size + 2)

    # reward_class = RankedRewardFactory(
    #         engine=engine,
    #         run_id=run_id,
    #         reward_buffer_min_size=32,
    #         reward_buffer_max_size=100,
    #         ranked_reward_alpha=0.9
    # )
    reward_class = LinearBoundedRewardFactory(min_reward=-env.size * 2, max_reward=0.)

    run_id = "gridworld_{}_{}".format(size, type(reward_class).__name__)
    policy_checkpoint_dir = "{}_policy_checkpoints".format(run_id)
    logger.info("run_id={}, policy_checkpoint_dir={}".format(run_id, policy_checkpoint_dir))

    problem = GridWorldProblem(
        env=env,
        engine=engine,
        reward_class=reward_class,
        run_id=run_id,
        min_buffer_size=10,
        max_buffer_size=32,
        batch_size=32,
        policy_checkpoint_dir=policy_checkpoint_dir
    )

    return problem


def run_games(size, use_mcts=False, num_mcts_samples=64, num_games=None, seed=None):
    np.random.seed(seed)

    if use_mcts:
        from rlmolecule.mcts.mcts import MCTS
        game = MCTS(construct_problem(size=size))
    else:
        from rlmolecule.alphazero.alphazero import AlphaZero
        game = AlphaZero(construct_problem(size=size), dirichlet_noise=False)

    # TODO: here, allow max games
    num_games = num_games if num_games is not None else sys.maxsize
    for _ in range(num_games):
        start_time = time.time()
        path, reward = game.run(num_mcts_samples=num_mcts_samples)
        elapsed = time.time() - start_time
        print("Worker {} | REWARD: {}   ".format(seed, reward.__dict__))
        logger.info(('Worker {} | Game {} Finished -- Reward {:.3f}'.format(seed, _, reward.raw_reward) +
                     # ' -- Final state {}'.format(path[-1]) +
                     ' -- CPU time {:1.3f} (s)'.format(elapsed)))


def train_model(size):
    construct_problem(size).train_policy_model(
        steps_per_epoch=200,
        game_count_delay=10,
        verbose=2)


def monitor(size):
    from rlmolecule.sql.tables import RewardStore
    problem = construct_problem(size=size)

    while True:
        best_reward = problem.session.query(RewardStore) \
            .filter_by(run_id=problem.run_id) \
            .order_by(RewardStore.reward.desc()).first()

        num_games = len(list(problem.iter_recent_games()))

        if hasattr(best_reward, "data") and "position" in best_reward.data:
            print(f"Best Reward: {best_reward.reward:.3f} for final position "
                  f"{best_reward.data['position']} with {num_games} games played")

        time.sleep(5)


def setup_argparser():
    return parser


if __name__ == "__main__":

    import argparse

    parser = argparse.ArgumentParser(
        description='Solve the Hallway problem (move from one side of the hallway to the other). ' +
                    'Default is to run multiple games and training using multiprocessing')

    parser.add_argument("--size", type=int, default=8)
    parser.add_argument('--train-policy',
                        action="store_true",
                        default=False,
                        help='Train the policy model only (on GPUs)')
    parser.add_argument('--rollout',
                        action="store_true",
                        default=False,
                        help='Run the game simulations only (on CPUs)')
    parser.add_argument('--num-workers', type=int, default=3, help='Number of multiprocessing workers')
    parser.add_argument("--num-games", type=int, default=None)
    parser.add_argument("--num-mcts-samples", type=int, default=50)
    parser.add_argument("--use-mcts", action="store_true")
    parser.add_argument("--log-level", default="INFO", type=str)

    args = parser.parse_args()

    logger.setLevel(level=getattr(logging, args.log_level.upper()))

    if args.train_policy:
        train_model(args.size)
    elif args.rollout:
        run_games(size=args.size,
                  use_mcts=args.use_mcts,
                  num_mcts_samples=args.num_mcts_samples,
                  num_games=args.num_games)
    else:
        assert args.num_workers >= 3, "need at least 3 workers for multiprocessing"

        import multiprocessing

        jobs = [multiprocessing.Process(target=monitor, args=(args.size,))]
        jobs[0].start()
        time.sleep(1)

        for i in range(args.num_workers - 2):
            jobs += [
                multiprocessing.Process(
                    target=run_games,
                    args=(args.size, args.use_mcts, args.num_mcts_samples, args.num_games, i)
                )
            ]

        jobs += [multiprocessing.Process(target=train_model, args=(args.size,))]

        for job in jobs[1:]:
            job.start()

        for job in jobs:
            job.join(300)
