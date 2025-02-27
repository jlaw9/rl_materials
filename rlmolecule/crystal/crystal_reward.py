
import logging
from collections import Counter
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
import tensorflow as tf
import tensorflow_addons as tfa
from tqdm import tqdm
import time
from pymatgen.core import Composition, Structure
from pymatgen.analysis.phase_diagram import PhaseDiagram, PDEntry
from pymatgen.analysis.structure_prediction.volume_predictor import DLSVolumePredictor
from nfp.preprocessing.crystal_preprocessor import PymatgenPreprocessor

from rlmolecule.crystal.crystal_state import CrystalState
from rlmolecule.crystal import reward_utils
from rlmolecule.crystal import ehull
from rlmolecule.tree_search.metrics import collect_metrics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@tf.function(experimental_relax_shapes=True)
def predict(model: 'tf.keras.Model', inputs):
    return model.predict_step(inputs)


class StructureRewardBattInterface:
    """ Compute the reward for crystal structures
    """

    def __init__(self,
                 competing_phases: List[PDEntry],
                 reward_weights: dict = None,
                 reward_cutoffs: dict = None,
                 cutoff_penalty: float = 2,
                 sub_rewards: Optional[List[str]] = None,
                 **kwargs) -> None:
        """ A class to estimate the suitability of a crystal structure as a solid state battery interface.

        :param competing_phases: list of competing phases used to 
            construct the convex hull for the elements of the given composition
        :param reward_weights: Weights specifying how the individual rewards will be combined.
        For example: `{"decomp_energy": 0.5, "cond_ion_frac": 0.1, [...]}`
        :param sub_rewards: Use only the specified sub rewards e.g., ['decomp_energy']
        """
        self.competing_phases = competing_phases
        self.reward_weights = reward_weights
        self.reward_cutoffs = reward_cutoffs
        self.sub_rewards = sub_rewards
        self.cutoff_penalty = cutoff_penalty
        # if the structure passes all cutoffs, then it gets a bonus
        # added to the combined reward
        self.cutoff_bonus = .25
        # For these rewards, smaller is better
        self.rewards_to_minimize = ['decomp_energy', 'oxidation']

        # set the weights of the individual rewards
        if self.reward_weights is None:
            self.reward_weights = {"decomp_energy": 2/3,
                                   "cond_ion_frac": 1/6,
                                   #"cond_ion_vol_frac": .1,
                                   "reduction": 1/18,
                                   "oxidation": 1/18,
                                   "stability_window": 1/18,
                                   }
        # set the cutoffs for the individual rewards
        # If the value does not fall in the desired range,
        # then apply a penalty.
        # The penalty is: scaled_reward / cutoff_penalty
        if self.reward_cutoffs is None:
            self.reward_cutoffs = {"decomp_energy": -0.1,
                                   "cond_ion_frac": .3,
                                   #"cond_ion_vol_frac": .3,
                                   "reduction": -2,
                                   "oxidation": -4,
                                   "stability_window": 2,
                                   }
        self.reward_ranges = {"decomp_energy": (-1, 5),
                              "cond_ion_frac": (0, 0.6),
                              #"cond_ion_vol_frac": (0, 0.8),
                              "reduction": (-5, 0),
                              "oxidation": (-5, 0),
                              "stability_window": (0, 5),
                              }
        self.default_decomp_energy = self.reward_ranges['decomp_energy'][1]

        # make sure the different reward dictionaries line up
        matching_keys = (set(self.reward_weights.keys())
                         & set(self.reward_cutoffs.keys())
                         & set(self.reward_ranges.keys()))
        assert len(matching_keys) == len(self.reward_weights), \
               (f"reward_weights (len = {len(self.reward_weights)}), "
                f"reward_cutoffs (len = {len(self.reward_cutoffs)}), "
                f"and reward_ranges (len = {len(self.reward_ranges)}), "
                f"must have matching keys. Keys that match all three: {matching_keys}")

        if self.sub_rewards is not None:
            num_matching = len(set(self.sub_rewards)
                               & set(self.reward_weights.keys()))
            assert num_matching == len(self.sub_rewards), \
                   "sub_rewards must be a subset of reward_weights"
            # don't use a cutoff bonus if not all subrewards are being used
            if len(self.sub_rewards) < len(self.reward_weights):
                print(f"Using {len(self.sub_rewards)} rewards: "
                      f"{self.sub_rewards}")
                print("Setting cutoff_bonus to 0")
                self.cutoff_bonus = 0

    def compute_reward(self,
                       structure: Structure,
                       predicted_energy: float = None,
                       state: CrystalState = None,
                       ):
        """
        The following sub-rewards are combined:
        1. Decomposition energy: predicts the total energy using a GNN model
            and calculates the corresponding decomposition energy based on the competing phases.
        2. Conducting ion fraction
        3. Conducting ion volume
        4. Reduction potential
        5. Oxidation potential
        6. Electrochemical stability window:
            difference between 4. and 5.
        
        Returns:
            float: reward
            dict: info
        """
        sub_rewards = {}
        info = {}
        if predicted_energy is None:
            decomp_energy = self.default_decomp_energy
        else:
            info.update({'predicted_energy': predicted_energy})
            decomp_energy, stability_window = ehull.convex_hull_stability(
                    structure.composition,
                    predicted_energy,
                    self.competing_phases,
            )
            if decomp_energy is None:
                # subtract 1 to the default energy to distinguish between
                # failed calculation here, and failing to decorate the structure 
                decomp_energy = self.default_decomp_energy - 1
            else:
                if decomp_energy < 0 and stability_window is not None:
                    oxidation, reduction = stability_window
                    stability_window_size = reduction - oxidation
                    sub_rewards.update({'oxidation': oxidation,
                                        'reduction': reduction,
                                        'stability_window': stability_window_size})

        sub_rewards['decomp_energy'] = decomp_energy

        try:
            cond_ion_frac = reward_utils.get_conducting_ion_fraction(structure.composition)
            sub_rewards['cond_ion_frac'] = cond_ion_frac

            #start = time.process_time()
            #cond_ion_vol_frac = reward_utils.compute_cond_ion_vol(structure, state=state)
            ## if the voronoi volume calculation failed, give a default of
            ## the conducting ion's fraction * .5
            #if cond_ion_vol_frac is None:
            #    cond_ion_vol_frac = cond_ion_frac / 2
            #sub_rewards['cond_ion_vol_frac'] = cond_ion_vol_frac
            #info['cond_ion_vol_time'] = time.process_time() - start

        # some structures don't have a conducting ion
        except ValueError as e:
            print(f"ValueError: {e}. State: {state}")

        info.update({s: round(r, 4) for s, r in sub_rewards.items()})
        if self.sub_rewards is not None:
            # limit to the specified sub rewards
            sub_rewards = {n: r for n, r in sub_rewards.items() if n in self.sub_rewards}
        combined_reward = self.combine_rewards(sub_rewards)
        #print(str(state), combined_reward, info)

        return combined_reward, info

    # This actually takes longer than just computing the convex hull each time
#    def precompute_convex_hulls(self, compositions):
#        self.phase_diagrams = {}
#        for comp in tqdm(compositions):
#            comp = Composition(comp)
#            elements = set(comp.elements)
#            curr_entries = [e for e in self.competing_phases
#                            if len(set(e.composition.elements) - elements) == 0]
#
#            phase_diagram = PhaseDiagram(curr_entries, elements=elements)
#            self.phase_diagrams[comp] = phase_diagram

    def combine_rewards(self, raw_scores, return_weighted=False) -> float:
        """ Take the weighted average of the normalized sub-rewards
        For example, decomposition energy: 1.2, conducting ion frac: 0.1.
        
        :param raw_scores: Dictionary with the raw scores
            for each type of sub-reward e.g., 'decomp_energy': 0.2
        :param return_weighted: Return the weighted sub-rewards 
            instead of their combined sum
        """
        scaled_rewards = {}
        passed_cutoffs = True
        for key, score in raw_scores.items():
            if score is None:
                continue

            reward = score
            # flip the score if lower is better,
            # so that a higher reward is better
            if key in self.rewards_to_minimize:
                reward = -1 * score

            # get the reward bounds
            r_min, r_max = self.reward_ranges[key]
            # also flip the ranges if necessary
            if key in self.rewards_to_minimize:
                r_max2 = -1 * r_min
                r_min = -1 * r_max
                r_max = r_max2

            assert r_max > r_min

            # apply the bounds to make sure the values are in the right range
            reward = max(r_min, reward)
            reward = min(r_max, reward)

            # scale between 0 and 1 using the given range of values
            scaled_reward = (reward - r_min) / (r_max - r_min)

            # If the value does not fall in the desired range,
            # then apply a penalty
            if key in self.reward_cutoffs and len(raw_scores) > 1:
                cutoff = self.reward_cutoffs[key]
                if key in self.rewards_to_minimize:
                    cutoff = -1 * cutoff
                if reward < cutoff:
                    scaled_reward /= float(self.cutoff_penalty)
                    passed_cutoffs = False

            scaled_rewards[key] = scaled_reward

        # Now apply the weights to each sub-reward
        weighted_rewards = {k: v * self.reward_weights[k]
                            for k, v in scaled_rewards.items()}
        combined_reward = sum([v * self.reward_weights[k]
                               for k, v in scaled_rewards.items()])
        # If this structure passed all the cutoffs,
        # then add a bonus to the reward
        if passed_cutoffs and len(raw_scores) > 1:
            combined_reward += self.cutoff_bonus
        #print(raw_scores)
        #print(scaled_rewards)
        #print(weighted_rewards)
        #print(combined_reward)
        if return_weighted:
            return weighted_rewards
        else:
            return combined_reward


class CrystalStateReward(StructureRewardBattInterface):
    """ Compute the reward for terminal states in the action space
    """

    def __init__(self,
                 competing_phases: List[PDEntry],
                 prototypes: Dict[str, Structure],
                 energy_model: 'tf.keras.Model',
                 preprocessor: PymatgenPreprocessor,
                 #dist_model: 'tf.keras.Model',
                 vol_pred_site_bias: Optional['pd.Series'] = None,
                 default_reward: float = 0,
                 **kwargs) -> None:
        """ A class to estimate the suitability of a crystal structure as a solid state battery interface.
        Starting from a terminal state, this class will build the structure, predict the total energy, 
        calculate the individual rewards, and combine them together.

        :param competing_phases: list of competing phases used to 
            construct the convex hull for the elements of the given composition
        :param prototypes: Dictionary mapping from prototype ID to structure. Used for decorating new structures
        :param energy_model: A tensorflow model to estimate the total energy of a structure
        :param preprocessor: Used to process the structure into inputs for the energy model

        :param vol_pred_site_bias: Optional argument of average volume per element (e.g., in ICSD).
            Used to predict the volume of the decorated structure 
            before passing it to the GNN. Uses the linear model + pymatgen's DLS predictor

        :param default_reward: Reward given to structures that failed to decorate
        """
        self.prototypes = prototypes
        self.energy_model = energy_model
        self.preprocessor = preprocessor
        #self.dist_model = dist_model
        self.vol_pred_site_bias = vol_pred_site_bias
        self.dls_vol_predictor = DLSVolumePredictor()
        self.default_reward = default_reward
        
        super(CrystalStateReward, self).__init__(competing_phases, **kwargs)

    @collect_metrics
    def get_reward(self, state: CrystalState) -> Tuple[float, dict]:
        """ Get the reward for the given crystal state. 
        This function first generates the structure by decorating the state's selected prototype.
        Then it predicts the total energy using the energy model 
        """
        if not state.terminal:
            return self.default_reward, {'terminal': False,
                                         'state_repr': repr(state)}
        info = {}
        structure = self.generate_structure(state)
        if structure is None:
            return self.default_reward, {'terminal': True,
                                         'state_repr': repr(state)}

        info.update({'terminal': True,
                     'num_sites': len(structure.sites),
                     'volume': structure.volume,
                     'state_repr': repr(state),
                     })

        # Predict the total energy of this decorated structure
        predicted_energy = self.predict_energy(structure, state)
        reward, reward_info = self.compute_reward(structure,
                                                  predicted_energy,
                                                  state)
        info.update(reward_info)
        return reward, info

    def generate_structure(self, state: CrystalState):
        # skip this structure if it is too large for the model.
        # I removed these structures from the action space (see builder.py "action_graph2_file"),
        # so shouldn't be a problem anymore
        structure_key = '|'.join(state.action_node.split('|')[:-1])
        icsd_prototype = self.prototypes[structure_key]

        # generate the decoration for this state
        try:
            structure = reward_utils.generate_decoration(state, icsd_prototype)
        except AssertionError as e:
            print(f"AssertionError: {e}")
            return

        if self.vol_pred_site_bias is not None:
            # predict the volume of the decorated structure before
            # passing it to the GNN. Use a linear model + pymatgen's DLS predictor
            structure = self.scale_by_pred_vol(structure)
        return structure

    def scale_by_pred_vol(self, structure: Structure) -> Structure:
        # first predict the volume using the average volume per element (from ICSD)
        site_counts = pd.Series(Counter(
            str(site.specie) for site in structure.sites)).fillna(0)
        curr_site_bias = self.vol_pred_site_bias[
            self.vol_pred_site_bias.index.isin(site_counts.index)]
        linear_pred = site_counts @ curr_site_bias
        structure.scale_lattice(linear_pred)

        # then apply Pymatgen's DLS predictor
        pred_volume = self.dls_vol_predictor.predict(structure)
        structure.scale_lattice(pred_volume)
        return structure

    def get_model_inputs(self, structure) -> dict:
        inputs = self.preprocessor(structure, train=False)
        # scale structures to a minimum of 1A interatomic distance
        min_distance = inputs["distance"].min()
        if np.isclose(min_distance, 0):
            # if some atoms are on top of each other, then this normalization fails
            # TODO remove those problematic prototype structures
            return None
            #raise RuntimeError(f"Error with {structure}")

        # only normalize if the volume is not being predicted
        if self.vol_pred_site_bias is None:
            inputs["distance"] /= inputs["distance"].min()
        return inputs

    @collect_metrics
    def predict_energy(self, structure: Structure, state=None) -> float:
        """ Predict the total energy of the structure using a GNN model (trained on unrelaxed structures)
        """
        model_inputs = self.get_model_inputs(structure)
        if model_inputs is None:
            return None, None
        # predicted_energy = predict(self.energy_model, model_inputs)
        dataset = tf.data.Dataset.from_generator(
            lambda: (s for s in [model_inputs]),
            #lambda: (self.preprocessor.construct_feature_matrices(s, train=False) for s in [structure]),
            output_signature=(self.preprocessor.output_signature),
            )
        dataset = dataset \
            .padded_batch(
                batch_size=1,
                padding_values=(self.preprocessor.padding_values),
            )
        predicted_energy = self.energy_model.predict(dataset)
        #print(f"{predicted_energy = }, {state = }")
        predicted_energy = predicted_energy[0][0].astype(float)

        return predicted_energy

#    @property
#    def reward(self) -> float:
#        """The reward function for the CrystalState graph.
#        Only states at the end of the graph have a reward,
#        since a decorated structure is required to compute the 
#        total energy and decomposition energy.
#
#        Returns:
#            float: 
#        """
#        return self.rewarder.get_reward(self)
