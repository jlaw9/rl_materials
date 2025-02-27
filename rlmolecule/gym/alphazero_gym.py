from abc import abstractmethod

import gym
import numpy as np


class AlphaZeroGymEnv(gym.Wrapper):
    """Simple wrapper class for a gym env to run with alphazero.  For 
    convenience you can either pass an env or a name that is available through 
    the standard gym env maker."""

    def __init__(self, env: gym.Env = None, name: str = None):
        env = env if env is not None else gym.make(name)
        super().__init__(env)

    @abstractmethod
    def get_obs(self) -> np.ndarray:
        """Returns an observation from the environment, similar to env.reset but
        returning the observation of the current state."""
        pass
