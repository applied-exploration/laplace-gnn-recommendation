import torch as t
import math
from torch_geometric.data import Data, HeteroData, InMemoryDataset
from torch import Tensor
from typing import Tuple, Union, Optional, List
from .matching.type import Matcher
from utils.constants import Constants
from config import Config
from utils.flatten import flatten
import random

device = t.device("cuda" if t.cuda.is_available() else "cpu")


class GraphDataset(InMemoryDataset):
    def __init__(
        self,
        config: Config,
        graph_path: str,
        users_adj_list: str,
        articles_adj_list: str,
        train: bool,
        matchers: Optional[List[Matcher]] = None,
        randomization: bool = True,
        split_type: Optional[str] = None,
    ):

        self.graph = t.load(graph_path)
        self.articles = t.load(articles_adj_list)
        self.users = t.load(users_adj_list)
        self.matchers = matchers
        self.config = config
        self.train = train
        self.randomization = randomization

    def __len__(self) -> int:
        return len(self.users)

    def __getitem__(self, idx: int) -> Union[Data, HeteroData]:
        """Create Edges"""
        all_edges = self.graph[Constants.edge_key].edge_index
        positive_article_indices = t.as_tensor(
            self.users[idx], dtype=t.long
        )  # all the positive target indices for the current user
        positive_article_edges = create_edges_from_target_indices(
            idx, positive_article_indices
        )

        # Sample positive edges from subgraph (amount defined in config.positive_edges_ratio)
        samp_cut = max(
            1,
            math.floor(
                len(positive_article_indices) * self.config.positive_edges_ratio
            ),
        )

        if self.randomization:
            random_integers = t.randint(
                low=0, high=len(positive_article_indices), size=(samp_cut,)
            )
        else:
            random_integers = t.tensor(
                [
                    t.min(positive_article_indices, dim=0)[1].item(),
                    t.max(positive_article_indices, dim=0)[1].item(),
                ]
            )

        sampled_positive_article_indices = positive_article_indices[random_integers]
        sampled_positive_article_edges = create_edges_from_target_indices(
            idx, sampled_positive_article_indices
        )

        num_sampled_pos_edges = sampled_positive_article_indices.shape[0]
        if num_sampled_pos_edges <= 1:
            negative_edges_ratio = self.config.k - 1
        else:
            negative_edges_ratio = self.config.negative_edges_ratio

        if self.train:
            # Randomly select from the whole graph
            sampled_negative_article_edges = create_edges_from_target_indices(
                idx,
                get_negative_edges_random(
                    subgraph_edges_to_filter=sampled_positive_article_indices,
                    all_edges=all_edges,
                    num_negative_edges=int(
                        negative_edges_ratio * num_sampled_pos_edges
                    ),
                    randomization=self.randomization,
                ),
            )
        else:
            assert self.matchers is not None, "Must provide matchers for test"
            # Select according to a heuristic (eg.: lightgcn scores)
            candidates = t.cat(
                [matcher.get_matches(idx) for matcher in self.matchers],
                dim=0,
            ).unique()
            # but never add positive edges
            sampled_negative_article_edges = create_edges_from_target_indices(
                idx,
                only_items_with_count_one(
                    t.cat([candidates, positive_article_indices], dim=0)
                ),
            )

        n_hop_edges = fetch_n_hop_neighbourhood(
            self.config.n_hop_neighbors,
            idx,
            self.users,
            self.articles,
            num_neighbors=self.config.num_neighbors,
        )

        all_touched_edges = t.cat(
            [
                positive_article_edges,
                sampled_negative_article_edges,
                n_hop_edges,
            ],
            dim=1,
        )

        all_subgraph_edges = t.cat(
            [
                positive_article_edges,
                n_hop_edges,
            ],
            dim=1,
        )

        """ Node Features """
        user_buckets = t.unique(all_touched_edges[0], sorted=True)
        article_buckets = t.unique(all_touched_edges[1], sorted=True)

        user_features = self.graph[Constants.node_user].x[user_buckets]
        article_features = self.graph[Constants.node_item].x[article_buckets]

        """ Remap and Prepare Edges """
        all_subgraph_edges = remap_edges_to_start_from_zero(
            all_subgraph_edges, user_buckets, article_buckets
        )
        all_sampled_edges = remap_edges_to_start_from_zero(
            t.cat(
                [sampled_positive_article_edges, sampled_negative_article_edges], dim=1
            ),
            user_buckets,
            article_buckets,
        )

        # Prepare identifier of labels
        labels = t.cat(
            [
                t.ones(sampled_positive_article_edges.shape[1]),
                t.zeros(sampled_negative_article_edges.shape[1]),
            ],
            dim=0,
        )

        # all_sampled_edges, labels = shuffle_edges_and_labels(all_sampled_edges, labels)

        """ Create Data """
        data = HeteroData()
        data[Constants.node_user].x = user_features
        data[Constants.node_item].x = article_features

        # Add original directional edges
        data[Constants.edge_key].edge_index = all_subgraph_edges.type(t.long)
        data[Constants.edge_key].edge_label_index = all_sampled_edges.type(t.long)
        data[Constants.edge_key].edge_label = labels.type(t.long)

        # Add reverse edges
        reverse_key = t.LongTensor([1, 0])
        data[Constants.rev_edge_key].edge_index = all_subgraph_edges[reverse_key].type(
            t.long
        )
        data[Constants.rev_edge_key].edge_label_index = all_sampled_edges[
            reverse_key
        ].type(t.long)
        data[Constants.rev_edge_key].edge_label = labels.type(t.long)
        return data


def only_items_with_count_one(input: t.Tensor) -> t.Tensor:
    uniques, counts = input.unique(return_counts=True)
    return uniques[counts == 1]


def get_negative_edges_random(
    subgraph_edges_to_filter: Tensor,
    all_edges: Tensor,
    num_negative_edges: int,
    randomization: bool,
) -> Tensor:

    # Get the biggest value available in articles (potential edges to sample from)
    id_max = t.max(all_edges, dim=1)[0][1]

    if all_edges.shape[1] / num_negative_edges > 100:
        # If the number of edges is high, it is unlikely we get a positive edge, no need for expensive filter operations
        if randomization:
            random_integers = t.randint(
                low=0, high=id_max.item(), size=(num_negative_edges,)
            )
        else:
            random_integers = t.tensor([id_max.item()])

        return random_integers

    else:
        # Create list of potential negative edges, filter out positive edges
        only_negative_edges = only_items_with_count_one(
            t.cat(
                (
                    t.arange(start=0, end=id_max + 1, dtype=t.int64),
                    subgraph_edges_to_filter,
                ),
                dim=0,
            )
        )

        # Randomly sample negative edges
        if randomization:
            random_integers = t.randperm(only_negative_edges.nelement())
            negative_edges = only_negative_edges[random_integers][:num_negative_edges]
        else:
            negative_edges = t.tensor([id_max.item()])

        return negative_edges


def remap_edges_to_start_from_zero(
    edges: Tensor, buckets_1st_dim: Tensor, buckets_2nd_dim: Tensor
) -> Tensor:
    return t.stack(
        (
            t.bucketize(edges[0], buckets_1st_dim),
            t.bucketize(edges[1], buckets_2nd_dim),
        )
    )


def create_edges_from_target_indices(
    source_index: int, target_indices: Tensor
) -> Tensor:
    """Expand target indices list with user's id to have shape [2, num_nodes]"""

    return t.stack(
        [
            t.Tensor([source_index]).to(dtype=t.long).repeat(len(target_indices)),
            t.as_tensor(target_indices, dtype=t.long),
        ],
        dim=0,
    )


def fetch_n_hop_neighbourhood(
    n: int, user_id: int, users: dict, articles: dict, num_neighbors: int
) -> t.Tensor:
    """Returns the edges from the n-hop neighbourhood of the user, without the direct links for the same user"""
    accum_edges = t.tensor([[], []], dtype=t.long)
    users_explored = set([])
    users_queue = set([user_id])

    for i in range(0, n):
        new_articles_and_edges = [
            create_neighbouring_article_edges(user, users) for user in users_queue
        ]
        users_explored = users_explored | users_queue
        if len(new_articles_and_edges) == 0:
            break
        new_articles = flatten([x[0] for x in new_articles_and_edges])

        if i != 0:
            new_edges = t.cat([x[1] for x in new_articles_and_edges], dim=1)
            accum_edges = t.cat([accum_edges, new_edges], dim=1)

        articles_queue = shuffle_and_cut(new_articles, num_neighbors)
        new_users = (
            set(flatten([articles[article] for article in articles_queue]))
            - users_explored
        )  # remove the intersection between the two sets, so we only explore a user once
        users_queue = set(shuffle_and_cut(list(new_users), num_neighbors))

    return accum_edges


def shuffle_and_cut(array: list, n: int) -> list:
    if len(array) > n:
        return random.sample(array, n)
    else:
        return array


def create_neighbouring_article_edges(
    user_id: int, users: dict
) -> Tuple[List[int], Tensor]:
    """Fetch neighbouring articles for a user, returns the article ids (list[int]) & edges (Tensor)"""
    articles_purchased = users[user_id]
    edges_to_articles = create_edges_from_target_indices(
        user_id, t.as_tensor(articles_purchased, dtype=t.long)
    )
    return articles_purchased, edges_to_articles


def shuffle_edges_and_labels(edges: Tensor, labels: Tensor) -> Tuple[Tensor, Tensor]:
    new_edge_order = t.randperm(edges.size(1))
    return (edges[:, new_edge_order], labels[new_edge_order])
