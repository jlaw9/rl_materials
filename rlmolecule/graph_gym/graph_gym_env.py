from typing import Tuple, Dict

import gym
import numpy as np
from gym.spaces import Box

from rlmolecule.graph_gym.graph_problem import GraphProblem
from rlmolecule.tree_search.graph_search_state import GraphSearchState


class GraphGymEnv(gym.Env):
    """

    """

    def __init__(self,
                 problem: GraphProblem,
                 ) -> None:
        super().__init__()
        self.problem: GraphProblem = problem
        self.state: GraphSearchState = self.problem.get_initial_state()
        self.action_space = problem.action_space
        self.observation_space: gym.Space = gym.spaces.Dict({
            'action_mask': Box(False, True, shape=(problem.max_num_actions,), dtype=np.bool),
            'action_observations': gym.spaces.Tuple((problem.observation_space,) * problem.max_num_actions),
            'state_observation': problem.observation_space
        })

    def reset(self) -> {str: np.ndarray}:
        self.state = self.problem.get_initial_state()
        return self.make_observation()

    def step(self, action: int) -> Tuple[Dict[str, np.ndarray], float, bool, dict]:
        next_actions = self.state.get_next_actions()

        reward, is_terminal, info = self.problem.invalid_action_result
        if action < len(next_actions):
            self.state = next_actions[action]  # assumes get_next_actions is indexable
            reward, is_terminal, info = self.problem.step(self.state)

        result = (self.make_observation(), reward, is_terminal, info)
        print(f'GraphGymEnv: {reward} {is_terminal}, {info}, {len(next_actions)}')
        return result

    def make_observation(self) -> {str: any}:
        max_num_actions = self.problem.max_num_actions
        action_mask = [False] * max_num_actions
        action_observations = [self.problem.null_observation] * max_num_actions

        for i, successor in enumerate(self.state.get_next_actions()):
            if i >= max_num_actions:
                break
            action_mask[i] = True
            action_observations[i] = self.problem.make_observation(successor)

        return {
            'action_mask': np.array(action_mask, dtype=np.bool),
            'action_observations': tuple(action_observations),
            'state_observation': self.problem.make_observation(self.state),
        }
